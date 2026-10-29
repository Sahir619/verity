import type { Candidate, RunState, StageId, StageStatus } from "@/lib/contracts/types";
import { isHalted } from "../figure/derive";

/** wait = not reached, run = in progress, done = finished, open = finished but the claim is not proven, skip = skipped, halt = the run stopped here. */
export type LedState = "wait" | "run" | "done" | "open" | "skip" | "halt";

export type LedColumn = { key: string; label: string; segs: number; state: LedState; word: string };
export type FailMark = { key: string; tag: string; text: string };
export type RoundInfo = { n: number; total: number | null; text: string };
export type LoopModel = { cols: LedColumn[]; fails: FailMark[]; round: RoundInfo; word: string };

export const SEGMENTS = 10;

const PENDING_CANDIDATE = new Set<Candidate["status"]>(["drafting", "linting", "simulating", "fixing", "fail"]);
/** Versions that have not yet been through the locked tests. A version that failed or is being fixed has run them. */
const UNRUN_CANDIDATE = new Set<Candidate["status"]>(["drafting", "linting", "simulating"]);

function stageValue(s: StageStatus): number {
  if (s === "done" || s === "skipped") return 1;
  if (s === "active" || s === "failed") return 0.5;
  return 0;
}

/** Mean progress of a group of stages, from their reported statuses only. */
function stageFrac(run: RunState, ids: StageId[]): number {
  return ids.reduce((sum, id) => sum + stageValue(run.stages[id].status), 0) / ids.length;
}

function allSkipped(run: RunState, ids: StageId[]): boolean {
  return ids.every((id) => run.stages[id].status === "skipped");
}

/** "R2 of 2" gives the number of rounds the run will play; null until the run says so. */
function totalRounds(run: RunState): number | null {
  const m = /of\s+(\d+)/i.exec(run.stages.evolve.detail ?? "");
  if (m) return Number(m[1]);
  return run.stages.evolve.status === "done" ? run.round : null;
}

function testedShare(cands: Candidate[]): number {
  return cands.length === 0 ? 0 : cands.filter((c) => !PENDING_CANDIDATE.has(c.status)).length / cands.length;
}

function ranShare(cands: Candidate[]): number {
  return cands.length === 0 ? 0 : cands.filter((c) => !UNRUN_CANDIDATE.has(c.status)).length / cands.length;
}

function column(key: string, label: string, frac: number, opts: { skipped?: boolean; halted: boolean; open?: boolean }): LedColumn {
  if (opts.skipped) return { key, label, segs: 0, state: "skip", word: "SKIP" };
  if (frac >= 1) return opts.open ? { key, label, segs: SEGMENTS, state: "open", word: "OPEN" } : { key, label, segs: SEGMENTS, state: "done", word: "DONE" };
  if (frac > 0) {
    const segs = Math.max(1, Math.ceil(frac * SEGMENTS));
    return opts.halted ? { key, label, segs, state: "halt", word: "HALT" } : { key, label, segs, state: "run", word: "RUN" };
  }
  return { key, label, segs: 0, state: "wait", word: "WAIT" };
}

/** The LOOP module's state, derived only from the run record (stages, candidates, verification, winner, equivalence). */
export function deriveLoop(run: RunState): LoopModel {
  const halted = isHalted(run);
  const ordered = run.candidateOrder.map((id) => run.candidates[id]).filter((c): c is Candidate => Boolean(c));
  const evolveStatus = run.stages.evolve.status;
  const proveStatus = run.stages.prove.status;
  const evolveSettled = evolveStatus === "done" || evolveStatus === "skipped" || proveStatus !== "pending" || run.phase === "done";
  const total = totalRounds(run);

  // TEST: every version has been through the locked tests. It stays open (never complete) while rounds are still adding versions.
  const testShare = ordered.length > 0 ? ranShare(ordered) : stageFrac(run, ["sim"]);
  const testFrac = testShare >= 1 && !evolveSettled ? 0.9 : testShare >= 1 ? 1 : Math.min(testShare, 0.9);

  // EVOLVE: finished rounds plus the share of the current round that has been tested.
  let evolveFrac = 0;
  if (evolveStatus === "done") evolveFrac = 1;
  else if (evolveStatus === "active" || run.round > 0) {
    const denom = total ?? Math.max(run.round, 1);
    const inRound = testedShare(ordered.filter((c) => c.round === run.round));
    evolveFrac = Math.min(0.95, Math.max(0.05, (run.round - 1 + inRound) / denom));
  }

  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  const proven = winner?.eqy?.status === "proven";
  const proveFrac = proveStatus === "done" ? 1 : proveStatus === "failed" ? 1 : proveStatus === "active" ? 0.5 : 0;

  const cols: LedColumn[] = [
    column("plan", "PLAN", stageFrac(run, ["plan"]), { halted }),
    column("lock", "LOCK", stageFrac(run, ["lock"]), { halted }),
    column("build", "BUILD", stageFrac(run, ["rtl", "lint", "synth"]), { halted, skipped: allSkipped(run, ["rtl", "lint", "synth"]) }),
    column("test", "TEST", testFrac, { halted, skipped: run.stages.sim.status === "skipped" && ordered.length === 0 }),
    column("evolve", "EVOLVE", evolveFrac, { halted, skipped: evolveStatus === "skipped" }),
    column("prove", "PROVE", proveFrac, { halted, skipped: proveStatus === "skipped", open: proveFrac >= 1 && !proven }),
  ];

  // Every failing attempt, in the order the run recorded them. A version that ended rejected says so.
  const fails: FailMark[] = [];
  for (const c of ordered) {
    c.attempts.forEach((a, i) => {
      const v = a.verification;
      if (v.lint !== "fail" && v.passed >= v.total) return;
      const last = i === c.attempts.length - 1;
      fails.push({ key: `${c.id}-${a.n}`, tag: `F${fails.length + 1}`, text: `${c.label}${last && c.status === "rejected" ? " rejected" : ""}` });
    });
  }

  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  let text: string;
  if (run.round === 0) {
    text = `Evolution starts after ${base?.label ?? "the first version"} is measured.`;
  } else {
    const inRound = ordered.filter((c) => c.round === run.round);
    const parents = [...new Set(inRound.map((c) => (c.parentId ? run.candidates[c.parentId]?.label : undefined)).filter((l): l is string => Boolean(l)))];
    const count = inRound.length;
    text = `${count} new ${count === 1 ? "version" : "versions"}${parents.length > 0 ? ` from ${parents.join(" and ")}` : ""}.`;
  }

  const word = halted
    ? "HALTED"
    : cols.every((c) => c.state === "done" || c.state === "open" || c.state === "skip")
      ? "COMPLETE"
      : cols.some((c) => c.state === "run")
        ? "RUNNING"
        : "STANDBY";

  return { cols, fails, round: { n: run.round, total, text }, word };
}
