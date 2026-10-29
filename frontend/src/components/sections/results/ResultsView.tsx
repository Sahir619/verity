"use client";

import { useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Callout } from "@/components/domain/Callout";
import { EmptyState } from "@/components/domain/EmptyState";
import { ModeBadge } from "@/components/domain/ModeBadge";
import { SampleDataBadge } from "@/components/domain/SampleDataBadge";
import { Button } from "@/components/ui/button";
import { AppHeader } from "@/components/sections/shared/AppHeader";
import { RackBarLink } from "@/components/sections/shared/RackBar";
import { RESULTS_ROOT } from "@/components/sections/shared/app-roots";
import { VignetteGrid } from "@/components/sections/shared/VignetteGrid";
import type { Candidate } from "@/lib/contracts/types";
import { useLiveRun } from "@/lib/hooks/use-live-run";
import { useReplayDriver } from "@/lib/hooks/use-replay-driver";
import { useRunSummary } from "@/lib/hooks/use-run-views";
import { useLive, usePlayback, useRun } from "@/lib/store";
import { ExportPanel } from "./ExportPanel";
import { RankingTable } from "./RankingTable";
import { ResultsSkeleton } from "./ResultsSkeleton";
import { RunSummaryPanel } from "./RunSummaryPanel";
import { WinnerPanel } from "./WinnerPanel";

function rankRows(order: string[], candidates: Record<string, Candidate>): Candidate[] {
  const all = order.map((id) => candidates[id]).filter((c): c is Candidate => Boolean(c));
  const scored = all
    .filter((c) => c.fitness !== null)
    .sort(
      (a, b) =>
        (b.fitness?.score ?? 0) - (a.fitness?.score ?? 0) ||
        (a.ppa?.areaUm2 ?? Infinity) - (b.ppa?.areaUm2 ?? Infinity),
    );
  const rest = all.filter((c) => c.fitness === null);
  return [...scored, ...rest];
}

/** Recordings under public/runs/. Any other `?run=` id is a live run. */
const REPLAY_RUNS = ["golden", "mac4-real"];

function ResultsReplay({ name }: { name: string }) {
  useReplayDriver({ src: `/runs/${name}.json`, autoplay: false, loop: false });
  const { status, tMs, durationMs, seek } = usePlayback();
  const jumped = useRef(false);

  // Results always shows the finished run: jump to the end once the recording is loaded.
  useEffect(() => {
    if (jumped.current || status === "loading" || status === "error" || durationMs <= 0) return;
    jumped.current = true;
    if (tMs < durationMs) seek(durationMs);
  }, [status, tMs, durationMs, seek]);

  const ready = durationMs > 0 && tMs >= durationMs - 1;
  if (status === "error") {
    return (
      <Frame>
        <EmptyState
          title="The recorded run could not be read."
          action={
            <Button variant="outline" onClick={() => window.location.reload()}>
              Try again
            </Button>
          }
        />
      </Frame>
    );
  }
  if (!ready) return <ResultsSkeleton />;
  return <ResultsBody runId={name} />;
}

function ResultsLive({ runId }: { runId: string }) {
  const storeRunId = useRun((r) => r.runId);
  const hasData = useRun((r) => r.candidateOrder.length > 0 || r.spec !== null);
  const { connection } = useLive();
  // Same session: the live store already holds this run, so keep it. A fresh load reconnects.
  useLiveRun(storeRunId === runId ? null : runId);

  if (!hasData && connection === "error") {
    return (
      <Frame>
        <EmptyState
          title="This live run is not available anymore."
          action={
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Button asChild className="h-10 px-4">
                <Link prefetch={false} href="/studio?run=mac4-real">Watch the recorded run</Link>
              </Button>
              <Button asChild variant="outline" className="h-10 px-4">
                <Link prefetch={false} href="/bench">Try live</Link>
              </Button>
            </div>
          }
        />
      </Frame>
    );
  }
  if (!hasData) return <ResultsSkeleton />;
  return <ResultsBody runId={runId} />;
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className={`${RESULTS_ROOT} flex min-h-svh flex-col`}>
      <VignetteGrid />
      <AppHeader />
      <main className="flex flex-1 items-center justify-center px-6 py-16">{children}</main>
    </div>
  );
}

function ResultsBody({ runId }: { runId: string }) {
  const summary = useRunSummary();
  const candidates = useRun((r) => r.candidates);
  const order = useRun((r) => r.candidateOrder);
  const baselineId = useRun((r) => r.baselineId);
  const round = useRun((r) => r.round);
  const priority = useRun((r) => r.spec?.priority ?? "area");
  const { steer, overrideWinnerLocal } = useLive();

  const rows = useMemo(() => rankRows(order, candidates), [order, candidates]);
  const baseline = baselineId ? (candidates[baselineId] ?? null) : null;
  const winner = summary.winnerId ? (candidates[summary.winnerId] ?? null) : null;
  const aiWinner = summary.aiWinnerId ? (candidates[summary.aiWinnerId] ?? null) : null;
  const finishedClean = summary.phase === "done";
  const partial = !finishedClean && (summary.phase === "cap_reached" || summary.phase === "stopped" || summary.phase === "error");
  const isLive = summary.mode === "live";

  const makeWinner = (id: string) => {
    if (isLive) void steer({ kind: "winner_override", candidateId: id });
    else overrideWinnerLocal(id);
  };

  const verification = winner?.verification ?? null;

  return (
    <div className={`${RESULTS_ROOT} flex min-h-svh flex-col`}>
      <VignetteGrid />
      <AppHeader
        current="results"
        context={
          <>
            <span className="truncate text-sm font-medium text-foreground">{summary.title}</span>
            <ModeBadge mode={summary.mode} />
            {summary.sample ? <SampleDataBadge /> : null}
          </>
        }
        actions={
          <RackBarLink href={`/studio?run=${encodeURIComponent(runId)}`}>Back to the run</RackBarLink>
        }
      />
      <main className="mx-auto w-full max-w-[80rem] flex-1 px-6 py-8">
        <h1 className="text-2xl font-bold text-foreground">Results</h1>

        <div className="mt-4 space-y-3 empty:hidden">
          {partial ? (
            <Callout
              tone={summary.phase === "stopped" ? "info" : "warn"}
              title={`Partial run: stopped during round ${round}.`}
              body={
                summary.phase === "cap_reached"
                  ? "The run reached its spend limit. The best version so far is shown."
                  : "Everything finished so far is kept."
              }
            />
          ) : null}
          {!winner ? (
            <Callout
              tone="fail"
              title="No version passed the locked tests."
              body="The run log and every attempt are still in the download."
            />
          ) : null}
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
          <div className="min-w-0 space-y-6">
            {winner ? (
              <WinnerPanel
                winner={winner}
                baseline={baseline}
                winnerBy={summary.winnerBy ?? "ai"}
                aiWinner={aiWinner}
                partial={partial}
                sample={summary.sample}
                priority={priority}
                parentLabel={winner.parentId ? (candidates[winner.parentId]?.label ?? null) : null}
              />
            ) : null}
            <RankingTable
              rows={rows}
              candidates={candidates}
              baseline={baseline}
              winnerId={summary.winnerId}
              winnerBy={summary.winnerBy}
              priority={priority}
              onMakeWinner={makeWinner}
            />
          </div>
          <div className="min-w-0 space-y-6">
            <RunSummaryPanel
              summary={summary}
              winnerTests={verification ? { passed: verification.passed, total: verification.total } : null}
            />
            <ExportPanel winnerLabel={winner?.label ?? null} baselineLabel={baseline?.label ?? null} />
          </div>
        </div>
      </main>
    </div>
  );
}

/** Client root of /results. `?run=mac4-real` (the default) or `?run=golden` shows the finished recording; any other id is a live run. */
export function ResultsView() {
  const runId = useSearchParams().get("run") ?? "mac4-real";
  return REPLAY_RUNS.includes(runId) ? <ResultsReplay key={runId} name={runId} /> : <ResultsLive key={runId} runId={runId} />;
}
