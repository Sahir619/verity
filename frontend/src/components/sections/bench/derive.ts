import type { Candidate, Equivalence, PPA, RunEvent, RunPhase, RunState } from "@/lib/contracts/types";
import type { Selection } from "@/lib/contracts/view-types";
import { formatDelta, formatTests } from "@/lib/format";

export const TERMINAL: ReadonlySet<RunPhase> = new Set(["done", "error", "cap_reached", "stopped"]);

/** The version the LIVE RTL module shows: the pinned one, else the one the run is following, else the latest. */
export function currentCandidate(run: RunState, pinned: Selection | null, follow: Selection | null): Candidate | null {
  for (const sel of [pinned, follow]) {
    if (sel?.kind === "candidate" && run.candidates[sel.id]) return run.candidates[sel.id];
  }
  const last = run.candidateOrder.at(-1);
  return last ? (run.candidates[last] ?? null) : null;
}

/**
 * Why a version was discarded, read from its own run record. A version dropped before any test has no record of its own,
 * so the agent's caption about it is used (`dropNotes`). Null for versions still in play.
 */
export function discardWhy(c: Candidate, run: RunState, dropNotes?: ReadonlyMap<string, string>): string | null {
  if (c.id === run.winnerId) return null;
  if (c.status === "rejected") {
    const v = c.attempts.at(-1)?.verification ?? c.verification;
    const tries = c.attempts.length === 1 ? "1 attempt" : `${c.attempts.length} attempts`;
    if (c.attempts.length === 0) return dropNotes?.get(c.label) ?? "Dropped before any locked test ran.";
    if (!v) return `Rejected after ${tries}.`;
    if (v.lint === "fail") {
      const line = v.lintLog?.split("\n").find((l) => l.trim() !== "")?.trim();
      return `Lint failed after ${tries}${line ? `: ${line}` : "."}`;
    }
    const first = v.failures[0];
    const tests = `${formatTests(v.passed, v.total)} locked tests passed after ${tries}`;
    return first ? `${tests}. First failure, ${first.name}: expected ${first.expected}, got ${first.got}.` : `${tests}.`;
  }
  if (c.status === "measured" && run.winnerId && TERMINAL.has(run.phase)) {
    const w = run.candidates[run.winnerId];
    if (c.fitness && w?.fitness) {
      return `Passed and measured, but did not beat ${w.label}: fitness ${c.fitness.score.toFixed(3)} against ${w.fitness.score.toFixed(3)} (score from measured gains vs v1, ${c.fitness.priority} priority).`;
    }
    return "Passed and measured, but not chosen.";
  }
  return null;
}

/** The agent's own caption for each version it dropped before testing, e.g. "v6 was identical to v2 and was dropped." */
export function dropNotesOf(log: RunEvent[]): Map<string, string> {
  const notes = new Map<string, string>();
  for (const e of log) {
    if (e.t !== "caption") continue;
    const m = /^(v\d+) .*\bdropped\b/.exec(e.text);
    if (m) notes.set(m[1], e.text);
  }
  return notes;
}

/** The last human_ref event in a live log (spec 5.3), or null when the agent has not sent one. */
export function humanRefOf(log: RunEvent[]): { ppa: PPA; eqy: Equivalence } | null {
  for (let i = log.length - 1; i >= 0; i--) {
    const e = log[i];
    if (e.t === "human_ref") return { ppa: e.ppa, eqy: e.eqy };
  }
  return null;
}

export type ScoreRow = {
  key: "area" | "clock" | "power";
  label: string;
  verity: number | null;
  human: number | null;
  /** Verity relative to the human reference; lower is better for all three. */
  delta: ReturnType<typeof formatDelta> | null;
};

export function scoreRows(winner: PPA, human: PPA): ScoreRow[] {
  const row = (key: ScoreRow["key"], label: string, v: number | null, h: number | null): ScoreRow => ({
    key,
    label,
    verity: v,
    human: h,
    delta: v !== null && h !== null ? formatDelta(v, h, { lowerIsBetter: true }) : null,
  });
  return [
    row("area", "Area", winner.areaUm2, human.areaUm2),
    row("clock", "Clock period", winner.clockPeriodNs, human.clockPeriodNs),
    row("power", "Power (estimate)", winner.powerMw, human.powerMw),
  ];
}
