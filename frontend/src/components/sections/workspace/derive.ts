import { familyById } from "@/lib/contracts/presets";
import { formatDelta } from "@/lib/format";
import type { RecentRun } from "@/lib/hooks/use-recent-runs";
import type { ServerRun } from "@/lib/sources/live";

/** The status square kinds the studio scope draws (.rkq). Amber is only `pass`. */
export type SqKind = "pass" | "fail" | "run" | "wait" | "drop" | "done";

/** What GET /runs gave, as far as the page can tell: not asked yet, not reachable, or answered. */
export type ServerState = "loading" | "unavailable" | "ready";

/** One run on the page: what this browser kept, joined by id to what the server says about it (if anything). */
export type WorkRun = {
  id: string;
  title: string;
  familyId: string;
  familyName: string;
  startedAt: number;
  server: ServerRun | null;
  status: { kind: SqKind; word: string };
  /** The run is done and the server names a winner: the only case that earns the amber square. */
  passed: boolean;
  /** Why there are no results on the card, or null when there are some (or none are expected yet). */
  missing: "not_on_server" | "not_loaded" | null;
};

/** Newest first. A run this browser listed but the server did not return stays, from local data. */
export function joinRuns(recent: RecentRun[], server: ServerRun[] | null, state: ServerState): WorkRun[] {
  const byId = new Map((server ?? []).map((s) => [s.id, s]));
  return [...recent]
    .sort((a, b) => b.startedAt - a.startedAt)
    .map((local) => {
      const remote = byId.get(local.id) ?? null;
      const status = statusOf(local, remote);
      return {
        id: local.id,
        title: local.title,
        familyId: local.familyId,
        familyName: familyById(local.familyId)?.name ?? local.familyId,
        startedAt: local.startedAt,
        server: remote,
        status,
        passed: remote !== null && remote.status === "done" && remote.winner != null,
        missing: remote ? null : state === "ready" ? "not_on_server" : state === "unavailable" ? "not_loaded" : null,
      };
    });
}

function statusOf(local: RecentRun, remote: ServerRun | null): { kind: SqKind; word: string } {
  if (remote) {
    switch (remote.status) {
      case "done":
        return remote.winner ? { kind: "pass", word: "Done" } : { kind: "done", word: "Done" };
      case "queued":
        return { kind: "run", word: "Queued" };
      case "running":
        return { kind: "run", word: "Running" };
      case "stopped":
        return { kind: "drop", word: "Stopped" };
      case "cap_reached":
        return { kind: "fail", word: "Stopped at the spend limit" };
      default:
        return { kind: "fail", word: "Failed" };
    }
  }
  // Only what this browser saw: it never earns the amber square, which needs the server's winner.
  if (local.ended === "done") return { kind: "done", word: "Done" };
  if (local.ended === "stopped") return { kind: "drop", word: "Stopped" };
  if (local.ended === "failed") return { kind: "fail", word: "Failed" };
  return { kind: "wait", word: "Status unknown" };
}

/** "v4 won", "v1 kept", or nothing when the run has no winner. */
export function winnerWords(run: WorkRun): string | null {
  const label = run.server?.winner?.label;
  if (!label) return null;
  if (label === "v1") return "v1 kept";
  return run.server?.status === "cap_reached" ? `${label} best so far` : `${label} won`;
}

/** A ratio against v1 as a signed percent text ("−1.6%", "+3.0%"), or null when it is not known. */
export function pctText(ratio: number | null | undefined): string | null {
  return ratio == null ? null : formatDelta(ratio, 1, { lowerIsBetter: true }).text;
}

/** A ratio against v1 as a percent number (−1.6 for 0.984), rounded to one decimal; null when it is not known. */
export function pctOf(ratio: number | null | undefined): number | null {
  return ratio == null ? null : Math.round((ratio - 1) * 1000) / 10;
}

export type Stats = {
  started: number;
  passed: number;
  /** Sum of the runs' own spend; null when no run has results. */
  spentUsd: number | null;
  /** How many of the runs the spend adds up. */
  spentFrom: number;
};

export function statsOf(runs: WorkRun[]): Stats {
  const spent = runs.flatMap((r) => (r.server?.outcome ? [r.server.outcome.usd] : []));
  return {
    started: runs.length,
    passed: runs.filter((r) => r.passed).length,
    spentUsd: spent.length > 0 ? spent.reduce((a, b) => a + b, 0) : null,
    spentFrom: spent.length,
  };
}

/** The family ids on the page, in order of first appearance, with their plain names. */
export function familiesOf(runs: WorkRun[]): { id: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const r of runs) if (!seen.has(r.familyId)) seen.set(r.familyId, r.familyName);
  return [...seen].map(([id, name]) => ({ id, name }));
}

export type DeltaRow = { id: string; title: string; area: number | null; clock: number | null };
export type SpendRow = { id: string; title: string; usd: number };

/** Runs with at least one measured change against their own v1, in percent. */
export function deltaRows(runs: WorkRun[]): DeltaRow[] {
  const out: DeltaRow[] = [];
  for (const r of runs) {
    const o = r.server?.outcome;
    if (!o) continue;
    const area = pctOf(o.areaVsV1);
    const clock = pctOf(o.clockVsV1);
    if (area !== null || clock !== null) out.push({ id: r.id, title: r.title, area, clock });
  }
  return out;
}

export function spendRows(runs: WorkRun[]): SpendRow[] {
  return runs.flatMap((r) => (r.server?.outcome ? [{ id: r.id, title: r.title, usd: r.server.outcome.usd }] : []));
}
