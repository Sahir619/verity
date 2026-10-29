"use client";

import { Suspense, useEffect, useState, type RefObject } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CirclePlay, House, PanelLeftClose, Plus, UserPlus, Users, X } from "lucide-react";
import { cpuJobActive, cpuSpecName, UPGRADE_LABELS, type CpuJobView } from "@/lib/contracts/cpu";
import type { LiveHealth } from "@/lib/contracts/view-types";
import { useAccount } from "@/lib/hooks/use-account";
import { useAccountLists } from "@/lib/hooks/use-account-lists";
import { useMediaQuery } from "@/lib/hooks/use-media-query";
import { clearRecentRuns, type RecentRun, type RunEnd } from "@/lib/hooks/use-recent-runs";
import { setSidebarShut } from "@/lib/hooks/use-sidebar";
import { clearThreads } from "@/lib/hooks/use-threads";
import { listMyCpuJobs, signOut } from "@/lib/sources/live";
import { Logo } from "../shared/Logo";
import { AdminUserModal } from "./AdminUserModal";
import { AdminUsersPanel } from "./AdminUsersPanel";
import { healthLine } from "./chat";

/** How a run ended, as one plain word beside its time. */
const END_WORD: Record<RunEnd, string> = { done: "Done", stopped: "Stopped", failed: "Failed" };

/** How a run the server lists ended (or that it is going), as one plain word. A status this bench does not know has no word. */
const SERVER_END_WORD: Record<string, string> = {
  done: "Done",
  stopped: "Stopped",
  cap_reached: "Spend limit",
  error: "Failed",
  timed_out: "Failed",
  queued: "Queued",
  running: "Running",
};

function startedAt(ms: number): string {
  const d = new Date(ms);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** An ISO time from the server as the same short label, or nothing when it does not read. */
function isoLabel(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? startedAt(ms) : "";
}

/** A CPU job's status as a shape plus one plain word, the same shapes the CPU board uses. */
const CPU_STATUS_WORD: Record<CpuJobView["status"], string> = {
  running: "Running",
  integrating: "Integrating",
  improving: "Improving",
  passed: "Passed",
  failed: "Failed",
  stopped: "Stopped",
};

function cpuStatusShape(status: CpuJobView["status"]): string {
  return status === "passed" ? "■" : status === "failed" || status === "stopped" ? "□" : "▣";
}

function cpuJobTitle(job: CpuJobView): string {
  if (job.upgradeId) return `CPU v2: ${UPGRADE_LABELS[job.upgradeId] ?? job.upgradeId}`;
  return job.retryOf ? `${cpuSpecName(job.specId)} (core retry)` : cpuSpecName(job.specId);
}

/**
 * The CPU jobs of this visitor (GET /cpu-jobs/mine, newest first): read on mount and whenever the open job changes (a job that
 * just started shows up) or the sign-in changes (a sign-out takes the account's jobs off the list), and again every 15 s while
 * any listed job is still active. A failed read leaves the list empty.
 */
function useCpuJobs(openJob: string | null, signedIn: boolean): CpuJobView[] {
  const [jobs, setJobs] = useState<CpuJobView[]>([]);
  const anyActive = jobs.some(cpuJobActive);

  useEffect(() => {
    const ac = new AbortController();
    void listMyCpuJobs(ac.signal).then((list) => {
      if (!ac.signal.aborted) setJobs(list);
    });
    return () => ac.abort();
  }, [openJob, signedIn]);

  useEffect(() => {
    if (!anyActive) return;
    const ac = new AbortController();
    const timer = window.setInterval(() => {
      void listMyCpuJobs(ac.signal).then((list) => {
        if (!ac.signal.aborted) setJobs(list);
      });
    }, 15_000);
    return () => {
      window.clearInterval(timer);
      ac.abort();
    };
  }, [anyActive]);

  return jobs;
}

/** The "CPU jobs" list. Reads ?cpuJob= itself, so it sits in its own Suspense boundary (the workspace page has none). Hidden while empty. */
function CpuJobsSection({ signedIn, onNavigate }: { signedIn: boolean; onNavigate(): void }) {
  const openCpuJob = useSearchParams().get("cpuJob");
  const cpuJobs = useCpuJobs(openCpuJob, signedIn);
  return (
    cpuJobs.length > 0 ? (
      <>
        <h2 className="b-side-h">CPU jobs</h2>
        <ul className="b-runs">
          {cpuJobs.map((j) => (
            <li key={j.id}>
              <Link
                prefetch={false}
                href={`/bench?cpuJob=${encodeURIComponent(j.id)}`}
                aria-current={j.id === openCpuJob ? "page" : undefined}
                title={cpuJobTitle(j)}
                onClick={onNavigate}
              >
                <span className="b-runs-t">{cpuJobTitle(j)}</span>
                <span className="b-runs-d">
                  <span aria-hidden>{cpuStatusShape(j.status)}</span> {[CPU_STATUS_WORD[j.status], isoLabel(j.createdAt)].filter(Boolean).join(", ")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </>
    ) : null
  );
}

/**
 * The bench's left side in place of the marketing bar: New chip, Home (the workspace page), Watch a run, the runs started
 * in this browser, and whether live runs are open. Its close button folds it away from 900px (the top bar brings it back)
 * and closes the drawer below that. Signed in with an account, the server's lists replace the browser's: "Chats" from
 * GET /threads and "Your runs" from GET /runs/mine, so they follow the account to another device; the foot then shows the
 * account and Sign out.
 */
export function Sidebar({
  runs,
  currentRun,
  currentThread = null,
  health,
  home = false,
  open,
  firstRef,
  onNewChip,
  onNavigate,
}: {
  runs: RecentRun[];
  currentRun: string | null;
  /** The chat in view (the bench only), marked in the Chats list. */
  currentThread?: string | null;
  health: LiveHealth;
  /** The workspace page is the one in view, so Home is the current page. */
  home?: boolean;
  /** The drawer is open (narrow screens only). */
  open: boolean;
  firstRef: RefObject<HTMLButtonElement | null>;
  onNewChip(): void;
  /** Closes the drawer: a link was followed, or its close button pressed. */
  onNavigate(): void;
}) {
  const router = useRouter();
  const wide = useMediaQuery("(min-width: 900px)");
  const account = useAccount();
  const signedIn = account.status === "signed_in";
  const lists = useAccountLists(signedIn);
  const [showAdminModal, setShowAdminModal] = useState(false);
  const [showAdminUsersPanel, setShowAdminUsersPanel] = useState(false);
  // The chats and runs kept in this browser are the account's cache: they go with the sign-out, so the next person here does not find them.
  const leave = async () => {
    await signOut();
    clearThreads();
    clearRecentRuns();
    router.replace("/signin");
    onNewChip(); // the account's open chat leaves the screen too (a code session in this tab starts a fresh thread)
  };
  return (
    <nav className="b-side" id="bench-side" aria-label="Workspace" data-open={open ? "" : undefined}>
      <div className="b-side-top">
        <Link prefetch={false} href="/" className="b-brand" aria-label="Verity home" onClick={onNavigate}>
          <Logo />
        </Link>
        <button
          type="button"
          className="b-ib"
          aria-label="Close the sidebar"
          onClick={() => {
            if (!wide) return onNavigate();
            setSidebarShut(true);
            requestAnimationFrame(() => document.getElementById("bench-menu")?.focus());
          }}
        >
          <X aria-hidden className="b-ico-n" />
          <PanelLeftClose aria-hidden className="b-ico-w" />
        </button>
      </div>
      <ul className="b-nav">
        <li>
          <button type="button" ref={firstRef} onClick={onNewChip}>
            <Plus aria-hidden />
            New chip
          </button>
        </li>
        <li>
          <Link prefetch={false} href="/workspace" aria-current={home ? "page" : undefined} onClick={onNavigate}>
            <House aria-hidden />
            Home
          </Link>
        </li>
        <li>
          <Link prefetch={false} href="/studio?run=mac4-real" onClick={onNavigate}>
            <CirclePlay aria-hidden />
            Watch a run
          </Link>
        </li>
      </ul>
      {signedIn ? (
        <>
          <h2 className="b-side-h">Chats</h2>
          {lists.threads.length > 0 ? (
            <ul className="b-runs">
              {lists.threads.map((t) => (
                <li key={t.id}>
                  <Link
                    prefetch={false}
                    href={`/bench?thread=${encodeURIComponent(t.id)}`}
                    aria-current={t.id === currentThread ? "page" : undefined}
                    title={t.title}
                    onClick={onNavigate}
                  >
                    <span className="b-runs-t">{t.title || "Untitled chat"}</span>
                    <span className="b-runs-d">{isoLabel(t.updatedAt)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="b-side-empty">{lists.threadsFailed ? "Your chats could not be loaded right now." : "Your chats appear here, on any device you sign in on."}</p>
          )}
        </>
      ) : null}
      <Suspense fallback={null}>
        <CpuJobsSection signedIn={signedIn} onNavigate={onNavigate} />
      </Suspense>
      <h2 className="b-side-h">Your runs</h2>
      {signedIn ? (
        lists.runs.length > 0 ? (
          <ul className="b-runs">
            {lists.runs.map((r) => (
              <li key={r.id}>
                <Link
                  prefetch={false}
                  href={`/bench?run=${encodeURIComponent(r.id)}`}
                  aria-current={r.id === currentRun ? "page" : undefined}
                  title={r.title}
                  onClick={onNavigate}
                >
                  <span className="b-runs-t">{r.title || "Untitled run"}</span>
                  <span className="b-runs-d">{[SERVER_END_WORD[r.status ?? ""], isoLabel(r.createdAt)].filter(Boolean).join(", ")}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="b-side-empty">{lists.runsFailed ? "Your runs could not be loaded right now." : "Runs you start appear here, on any device you sign in on."}</p>
        )
      ) : runs.length > 0 ? (
        <ul className="b-runs">
          {runs.map((r) => (
            <li key={r.id}>
              <Link
                prefetch={false}
                href={`/bench?run=${encodeURIComponent(r.id)}`}
                aria-current={r.id === currentRun ? "page" : undefined}
                title={r.title}
                onClick={onNavigate}
              >
                <span className="b-runs-t">{r.title}</span>
                <span className="b-runs-d">
                  {r.ended ? `${END_WORD[r.ended]}, ` : ""}
                  {startedAt(r.startedAt)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="b-side-empty">Runs you start in this browser appear here.</p>
      )}
      <div className="b-health">
        <p role="status">{healthLine(health)}</p>
        {signedIn ? (
          <div className="b-acct" style={{ flexDirection: "column", alignItems: "stretch", gap: "6px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
              <p title={account.email ?? undefined} style={{ minWidth: 0, flex: 1 }}>{account.email}</p>
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
              {account.isAdmin ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: "10px" }}>
                  <button
                    type="button"
                    className="b-link"
                    onClick={() => setShowAdminModal(true)}
                    style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: "var(--rk-a)" }}
                  >
                    <UserPlus style={{ width: "12px", height: "12px" }} />
                    Add user
                  </button>
                  <button
                    type="button"
                    className="b-link"
                    onClick={() => setShowAdminUsersPanel(true)}
                    style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: "var(--rk-a)" }}
                  >
                    <Users style={{ width: "12px", height: "12px" }} />
                    Accounts
                  </button>
                </span>
              ) : <span />}
              <button type="button" className="b-link" onClick={() => void leave()}>
                Sign out
              </button>
            </div>
            <AdminUserModal isOpen={showAdminModal} onClose={() => setShowAdminModal(false)} />
            <AdminUsersPanel isOpen={showAdminUsersPanel} onClose={() => setShowAdminUsersPanel(false)} />
          </div>
        ) : null}
      </div>
    </nav>
  );
}
