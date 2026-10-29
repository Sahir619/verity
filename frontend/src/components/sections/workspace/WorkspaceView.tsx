"use client";

import { useMemo, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLiveHealth } from "@/lib/hooks/use-live-health";
import { useRecentRuns } from "@/lib/hooks/use-recent-runs";
import { setOpenThread } from "@/lib/hooks/use-threads";
import { useRunsList } from "@/lib/hooks/use-runs-list";
import { resumeRun } from "@/lib/sources/live";
import { AppShell } from "../bench/AppShell";
import { Sidebar } from "../bench/Sidebar";
import { formatSmallUsd } from "../bench/chat";
import "../bench/bench.css";
import "./workspace.css";
import { DeltaChart, SpendChart } from "./Charts";
import { CombinePreview } from "./CombinePreview";
import { RunCard } from "./RunCard";
import { deltaRows, familiesOf, joinRuns, spendRows, statsOf, type ServerState } from "./derive";

/** False on the server and during hydration, true after: the browser-only list is not read as "empty" before it is read. */
function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

/**
 * /workspace, the page behind the sidebar's Home: every chip started in this browser, newest first, joined by run id to what
 * the server says about it (GET /runs). Cards open the run in the bench. Two charts read the same runs against each run's
 * own v1 and against its spend; filters sit above everything they scope. Combine is a preview of an idea, marked as such.
 * The server's spend limits are never shown as figures; only what runs actually spent is.
 */
export function WorkspaceView() {
  const router = useRouter();
  const health = useLiveHealth();
  const recent = useRecentRuns();
  const hydrated = useHydrated();
  const { list, reload } = useRunsList();
  const [family, setFamily] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerFirstRef = useRef<HTMLButtonElement>(null);
  const [resumingId, setResumingId] = useState<string | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);

  const handleResume = async (id: string) => {
    setResumingId(id);
    setResumeError(null);
    const res = await resumeRun(id);
    setResumingId(null);
    if ("error" in res) {
      setResumeError(res.error);
      return;
    }
    router.push(`/bench?run=${encodeURIComponent(res.runId)}`);
  };

  const serverState: ServerState = list.status === "ready" ? "ready" : list.status === "loading" ? "loading" : "unavailable";
  const all = useMemo(() => joinRuns(recent, list.status === "ready" ? list.runs : null, serverState), [recent, list, serverState]);
  const families = useMemo(() => familiesOf(all), [all]);
  const active = family !== null && families.some((f) => f.id === family) ? family : null;
  const runs = useMemo(() => (active ? all.filter((r) => r.familyId === active) : all), [all, active]);
  const stats = useMemo(() => statsOf(runs), [runs]);
  const deltas = useMemo(() => deltaRows(runs), [runs]);
  const spends = useMemo(() => spendRows(runs), [runs]);
  const loading = list.status === "loading";

  let body;
  if (!hydrated) {
    body = (
      <p className="w-status" role="status">
        Loading your runs
      </p>
    );
  } else if (all.length === 0) {
    body = (
      <div className="w-empty">
        <p>No runs from this browser yet.</p>
        <Link prefetch={false} href="/bench" className="bx pri" onClick={() => setOpenThread(null)}>
          New chip
        </Link>
      </div>
    );
  } else {
    body = (
      <>
        {list.status === "error" || list.status === "off" ? (
          <p className="w-banner" role="status">
            {list.status === "off"
              ? "Live runs are off on this site, so there are no results to show. Your runs are listed from this browser."
              : "Verity's server did not answer, so results are not shown. Your runs are listed from this browser."}
            {list.status === "error" ? (
              <button type="button" className="b-link" onClick={reload}>
                Try again
              </button>
            ) : null}
          </p>
        ) : null}

        <div className="w-bar">
          {families.length > 1 ? (
            <div className="w-filter" role="group" aria-label="Show chips of one kind">
              <button type="button" className="w-chip" aria-pressed={active === null} onClick={() => setFamily(null)}>
                All
              </button>
              {families.map((f) => (
                <button key={f.id} type="button" className="w-chip" aria-pressed={active === f.id} onClick={() => setFamily(f.id)}>
                  {f.name}
                </button>
              ))}
            </div>
          ) : null}

          {!loading ? (
            <dl className="w-stats">
              <div className="w-stat">
                <dt>{stats.started === 1 ? "block started" : "blocks started"}</dt>
                <dd>{stats.started}</dd>
              </div>
              <div className="w-stat">
                <dt>passed their tests</dt>
                <dd>{list.status === "ready" ? stats.passed : "n/a"}</dd>
              </div>
              <div className="w-stat">
                <dt>
                  spent
                  {stats.spentUsd !== null && stats.spentFrom < stats.started ? ` (from ${stats.spentFrom} of ${stats.started} runs)` : ""}
                </dt>
                <dd>{stats.spentUsd === null ? "n/a" : formatSmallUsd(stats.spentUsd)}</dd>
              </div>
            </dl>
          ) : null}
        </div>

        <section aria-labelledby="w-runs-h">
          <h2 className="sr" id="w-runs-h">
            Runs
          </h2>
          <ul className="w-cards">
            {runs.map((r) => (
              <RunCard key={r.id} run={r} loading={loading} resuming={resumingId === r.id} onResume={handleResume} />
            ))}
          </ul>
          {resumeError ? <p className="w-status" role="alert">{resumeError}</p> : null}
        </section>

        {loading ? (
          <p className="w-status" role="status">
            Loading results from the server
          </p>
        ) : (
          <div className="w-charts">
            {deltas.length > 0 ? (
              <DeltaChart rows={deltas} />
            ) : (
              <p className="w-status">
                {list.status === "ready"
                  ? "No results to chart yet."
                  : "Charts need the server's results, which could not be loaded."}
              </p>
            )}
            {spends.length > 0 ? <SpendChart rows={spends} /> : null}
          </div>
        )}
      </>
    );
  }

  return (
    <AppShell
      sidebar={
        <Sidebar
          runs={recent}
          currentRun={null}
          health={health}
          home
          open={drawerOpen}
          firstRef={drawerFirstRef}
          onNewChip={() => {
            setOpenThread(null); // New chip is a new thread, not the one left open in the bench
            router.push("/bench");
          }}
          onNavigate={() => setDrawerOpen(false)}
        />
      }
      panel={null}
      drawerOpen={drawerOpen}
      onDrawer={setDrawerOpen}
      drawerFirstRef={drawerFirstRef}
      title={null}
      empty={false}
      label="Your workspace"
    >
      <div className="b-scroll">
        <div className="w-page">
          <header className="w-head">
            <h1>Your workspace</h1>
            <p>Every chip you started in this browser, newest first.</p>
          </header>
          {body}
          <CombinePreview />
        </div>
      </div>
    </AppShell>
  );
}
