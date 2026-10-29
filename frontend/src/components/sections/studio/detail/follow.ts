"use client";

import { useState } from "react";
import type { Candidate, RunState } from "@/lib/contracts/types";
import type { Selection } from "@/lib/contracts/view-types";

/**
 * "Following the run": which item the detail panel shows when nothing is pinned. The run state carries no
 * per-item timestamps, so the panel compares each state with the previous one and moves to what just changed.
 * Seeking backwards (lastSeq drops) starts over from a static reading of the state, so a seek never leaves the
 * panel on an item from the future.
 */

type Track = {
  run: RunState;
  seq: number;
  sigs: Record<string, string>;
  winnerId: string | null;
  planRevision: number;
  lock: string;
  focus: Selection | null;
};

const RUNNING = new Set<Candidate["status"]>(["drafting", "linting", "simulating", "fixing", "synthesizing", "pass"]);

function sigOf(c: Candidate): string {
  return `${c.status}|${c.attempts.length}|${c.fitness?.score ?? ""}|${c.eqy?.status ?? ""}`;
}

/** The self-fix story outranks routine progress; a measured result outranks a draft starting. */
function importance(c: Candidate): number {
  if (c.status === "fail" || c.status === "fixing" || c.status === "rejected") return 3;
  if (c.attempts.length > 1 && (c.status === "pass" || c.status === "synthesizing" || c.status === "measured")) return 2;
  if (c.status === "measured") return 1;
  return 0;
}

function planFocus(run: RunState): Selection | null {
  const id = run.plan.lastDiff?.changed[0] ?? run.plan.lastDiff?.added[0] ?? run.plan.blocks[0]?.id;
  return id ? { kind: "block", id } : null;
}

/** A static reading of the state, used on the first frame and after a backwards seek. */
function staticFocus(run: RunState): Selection | null {
  const order = run.candidateOrder.map((id) => run.candidates[id]).filter((c): c is Candidate => Boolean(c));
  const failing = order.filter((c) => c.status === "fail" || c.status === "fixing").at(-1);
  if (failing) return { kind: "candidate", id: failing.id };
  if (run.winnerId && run.candidates[run.winnerId]) return { kind: "candidate", id: run.winnerId };
  const running = order.filter((c) => RUNNING.has(c.status)).at(-1);
  if (running) return { kind: "candidate", id: running.id };
  const last = order.at(-1);
  if (last) return { kind: "candidate", id: last.id };
  if (run.stages.lock.status !== "pending") return { kind: "golden", id: "golden" };
  if (run.plan.blocks.length > 0) return planFocus(run);
  return run.spec ? { kind: "spec", id: "spec" } : null;
}

function snapshot(run: RunState): Pick<Track, "seq" | "sigs" | "winnerId" | "planRevision" | "lock"> {
  const sigs: Record<string, string> = {};
  for (const id of run.candidateOrder) {
    const c = run.candidates[id];
    if (c) sigs[id] = sigOf(c);
  }
  return { seq: run.lastSeq, sigs, winnerId: run.winnerId, planRevision: run.plan.revision, lock: run.stages.lock.status };
}

function step(prev: Track | null, run: RunState): Track {
  const snap = snapshot(run);
  if (!prev || run.lastSeq < prev.seq) return { run, ...snap, focus: staticFocus(run) };

  let focus = prev.focus;
  // The most important changed version; on a tie, the later one in the run.
  let bestId: string | null = null;
  let bestImp = -1;
  for (const id of run.candidateOrder) {
    const c = run.candidates[id];
    if (!c || prev.sigs[id] === snap.sigs[id]) continue;
    const imp = importance(c);
    if (imp >= bestImp) {
      bestId = id;
      bestImp = imp;
    }
  }

  if (run.winnerId && run.winnerId !== prev.winnerId) focus = { kind: "candidate", id: run.winnerId };
  else if (bestId) focus = { kind: "candidate", id: bestId };
  else if (snap.lock !== prev.lock && snap.lock !== "pending") focus = { kind: "golden", id: "golden" };
  else if (snap.planRevision !== prev.planRevision) focus = planFocus(run) ?? focus;
  else if (!focus && run.spec) focus = { kind: "spec", id: "spec" };

  return { run, ...snap, focus };
}

/**
 * The followed item for the current run state. State is adjusted during render (React's "store information from
 * previous renders" pattern), so the panel switches in the same frame as the figure, with no effect and no flash.
 */
export function useFollowFocus(run: RunState): Selection | null {
  const [track, setTrack] = useState<Track>(() => step(null, run));
  if (track.run !== run) {
    const next = step(track, run);
    setTrack(next);
    return next.focus;
  }
  return track.focus;
}
