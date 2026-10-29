import type { Candidate, Port, Priority, RunState, StageStatus } from "@/lib/contracts/types";

/** pending = not reached (hollow), current = the step the run is on (full ink), done = finished, skipped = the run skipped it. */
export type StepState = "pending" | "current" | "done" | "skipped";

export type FigureColumn =
  | { kind: "spec"; key: "spec"; label: string; state: StepState }
  | { kind: "plan"; key: "plan"; label: string; state: StepState }
  | { kind: "lock"; key: "lock"; label: string; state: StepState }
  | { kind: "build"; key: "build"; label: string; state: StepState; candidates: Candidate[] }
  | { kind: "round"; key: string; label: string; state: StepState; round: number; candidates: Candidate[] }
  /** Before the first round event: one hollow column, so every loop step is labeled from the first frame. */
  | { kind: "rounds"; key: "rounds"; label: string; state: StepState }
  | { kind: "prove"; key: "prove"; label: string; state: StepState };

const BUILD_STAGES = ["rtl", "lint", "sim", "fix", "synth"] as const;

function fromStage(status: StageStatus): StepState {
  if (status === "done" || status === "failed") return "done";
  if (status === "skipped") return "skipped";
  return status === "active" ? "current" : "pending";
}

/** A run that ended early (stopped, error, credit cap). Its current step stays current; nothing is promoted to done. */
export function isHalted(run: RunState): boolean {
  return run.phase === "error" || run.phase === "cap_reached" || run.phase === "stopped";
}

/**
 * Columns of Figure 1, derived only from run data. The number of round columns comes from the round events
 * (run.round) and the candidates' own round numbers, never from a constant. A single refine chain (one version
 * per round) renders as one straight line through the round columns.
 */
export function deriveColumns(run: RunState): FigureColumn[] {
  const { stages, candidates, candidateOrder } = run;
  const ordered = candidateOrder.map((id) => candidates[id]).filter((c): c is Candidate => Boolean(c));

  let rounds = run.round;
  for (const c of ordered) if (c.round > rounds) rounds = c.round;

  const proveState = fromStage(stages.prove.status);
  const evolveStarted = stages.evolve.status !== "pending";
  const evolveDone = stages.evolve.status === "done" || proveState !== "pending";

  const buildCandidates = ordered.filter((c) => c.round === 0);
  const buildActive = buildCandidates.length > 0 || BUILD_STAGES.some((s) => stages[s].status !== "pending");
  const buildState: StepState = rounds > 0 || evolveStarted || proveState !== "pending" ? "done" : buildActive ? "current" : "pending";

  const cols: FigureColumn[] = [
    { kind: "spec", key: "spec", label: "Spec", state: fromStage(stages.spec.status) },
    { kind: "plan", key: "plan", label: "Plan", state: fromStage(stages.plan.status) },
    { kind: "lock", key: "lock", label: "Lock", state: fromStage(stages.lock.status) },
    { kind: "build", key: "build", label: "Build", state: buildState, candidates: buildCandidates },
  ];

  if (rounds === 0) {
    cols.push({ kind: "rounds", key: "rounds", label: "Rounds", state: stages.evolve.status === "skipped" ? "skipped" : "pending" });
  } else {
    for (let n = 1; n <= rounds; n++) {
      const state: StepState = n < run.round || evolveDone ? "done" : n === run.round ? "current" : "pending";
      cols.push({ kind: "round", key: `round-${n}`, label: `Round ${n}`, state, round: n, candidates: ordered.filter((c) => c.round === n) });
    }
  }

  cols.push({ kind: "prove", key: "prove", label: "Prove", state: proveState });

  // A step that is reached but not yet marked by the stage stream (spec at t=0, say) still reads as done once a later
  // step has started, so the figure never shows a hollow step behind the current one.
  let seenLater = false;
  for (let i = cols.length - 1; i >= 0; i--) {
    const col = cols[i];
    if (seenLater && col.state === "pending" && col.kind !== "rounds") col.state = "done";
    if (col.state !== "pending") seenLater = true;
  }
  return cols;
}

/** Template for the grid: early steps are narrow, version columns share the rest. */
export function gridTemplate(cols: FigureColumn[]): string {
  return cols
    .map((c) => {
      switch (c.kind) {
        case "spec":
          return "minmax(112px, .8fr)";
        case "plan":
          return "minmax(126px, .9fr)";
        case "lock":
          return "minmax(104px, .72fr)";
        case "prove":
          return "minmax(118px, .8fr)";
        default:
          return "minmax(140px, 1fr)";
      }
    })
    .join(" ");
}

/** "a[7:0] x4, b[7:0] x4" and "acc[21:0]": data ports only, single-bit control ports left out. */
export function portLines(ports: Port[]): [string, string] {
  const text = (p: Port) => `${p.name}[${p.width - 1}:0]${p.count && p.count > 1 ? ` x${p.count}` : ""}`;
  const data = ports.filter((p) => p.width > 1 || (p.count ?? 1) > 1);
  const use = data.length > 0 ? data : ports;
  return [use.filter((p) => p.dir === "in").map(text).join(", "), use.filter((p) => p.dir === "out").map(text).join(", ")];
}

export function goalText(priority: Priority): string {
  return priority === "area" ? "Smallest area" : priority === "speed" ? "Fastest clock" : "Lowest power";
}

/** "Rejected after 2 attempts" only when the last attempt failed the tests; other rejections just say "Rejected". */
export function rejectedText(c: Candidate): string {
  const last = c.attempts.at(-1)?.verification;
  const failed = last ? last.lint === "fail" || last.passed < last.total : false;
  const n = c.attempts.length;
  return failed && n > 0 ? `Rejected after ${n} ${n === 1 ? "attempt" : "attempts"}` : "Rejected";
}

export function fixedText(n: number): string | null {
  if (n <= 0) return null;
  if (n === 1) return "Fixed once";
  if (n === 2) return "Fixed twice";
  return `Fixed ${n} times`;
}
