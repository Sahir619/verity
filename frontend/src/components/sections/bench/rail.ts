import type { WalkStepId } from "@/lib/contracts/ask";
import type { Candidate, Equivalence, PPA, RunEvent, RunState, StageStatus } from "@/lib/contracts/types";
import type { StepKind } from "./chat";
import { TERMINAL } from "./derive";
import { signoffClean, type SignoffView } from "./Signoff";

/** The engineer's steps on the Architecture tab: Verity's nine, then two it does not run yet. */
export type RailId = WalkStepId | "assertions" | "coverage";
/** "done": the step ran to its end; a plain ink square, because amber is for a passed or verified result only. */
export type RailKind = StepKind | "done";
export type RailStep = { id: RailId; title: string; kind: RailKind; word: string; detail: string | null; off?: boolean };

export const RAIL_TITLES: Record<RailId, string> = {
  spec: "Spec",
  plan: "Plan",
  rtl: "RTL",
  lint: "Lint",
  simulate: "Simulate",
  synth: "Synthesize",
  improve: "Improve",
  equivalence: "Equivalence",
  signoff: "Sign-off",
  assertions: "Assertions",
  coverage: "Coverage",
};

/** A stage of the run as a shape and a word. A stage still pending when the run ended was never reached. */
function fromStage(status: StageStatus, ended: boolean): { kind: RailKind; word: string } {
  switch (status) {
    case "done":
      return { kind: "done", word: "Done" };
    case "active":
      return ended ? { kind: "fail", word: "Did not finish" } : { kind: "run", word: "Running" };
    case "failed":
      return { kind: "fail", word: "Failed" };
    case "skipped":
      return { kind: "drop", word: "Skipped" };
    case "pending":
      return ended ? { kind: "drop", word: "Not reached" } : { kind: "wait", word: "Waiting" };
  }
}

/** The agent's own line about the first drafts, e.g. "Wrote 3 first versions in parallel. ...". */
export function draftsLine(log: RunEvent[]): string | null {
  for (const e of log) if (e.t === "caption" && /^Wrote \d+ first versions?\b/.test(e.text)) return e.text;
  return null;
}

const BUSY = new Set<Candidate["status"]>(["drafting", "linting", "simulating", "fixing", "synthesizing"]);

function proofWord(status: Equivalence["status"]): { kind: StepKind; word: string } {
  if (status === "proven") return { kind: "pass", word: "Proven" };
  if (status === "not_equivalent") return { kind: "fail", word: "Differs" };
  if (status === "inconclusive") return { kind: "fail", word: "Not proven" };
  return { kind: "drop", word: "Not run" };
}

/**
 * The rail, read from the run only. Lint, Simulate and Synthesize describe the version in view (`c`); the others describe
 * the run. No step claims more than its record: a step the run never reached says so.
 */
export function railSteps(
  run: RunState,
  c: Candidate | null,
  ctx: { signoff: SignoffView; human: { ppa: PPA; eqy: Equivalence } | null; drafts: string | null; rules: number | null },
): RailStep[] {
  const ended = TERMINAL.has(run.phase);
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const baseLabel = base?.label ?? "v1";
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  const of = c ? `, ${c.label}` : "";
  const steps: RailStep[] = [];
  const push = (id: RailId, s: { kind: RailKind; word: string }, detail: string | null) => steps.push({ id, title: RAIL_TITLES[id], ...s, detail });

  // Spec: the request read against the locked pack.
  push(
    "spec",
    run.spec ? { kind: "done", word: "Done" } : fromStage(run.stages.spec.status, ended),
    run.spec ? (ctx.rules !== null ? `${ctx.rules} rules from the locked pack` : run.spec.title) : null,
  );

  const blocks = run.plan.blocks.length;
  push("plan", fromStage(run.stages.plan.status, ended), blocks > 0 ? `${blocks} blocks, in plan order` : null);

  const written = run.candidateOrder.length;
  push(
    "rtl",
    run.baselineId ? { kind: "done", word: "Done" } : fromStage(run.stages.rtl.status, ended),
    written > 0 ? (ctx.drafts ? `${ctx.drafts.split(".")[0]}; ${written} versions in all` : `${written} ${written === 1 ? "version" : "versions"} written`) : null,
  );

  // The version in view: lint, the locked tests, and the measurement.
  const v = c?.verification ?? null;
  const busy = c ? BUSY.has(c.status) && !ended : false;
  const sheet = ctx.signoff.state === "ok" && c && ctx.signoff.sheet.candidateId === c.id ? ctx.signoff.sheet : null;
  if (!c) push("lint", ended ? { kind: "drop", word: "Not reached" } : { kind: "wait", word: "Waiting" }, null);
  else if (!v) push("lint", busy ? { kind: "run", word: "Running" } : { kind: ended ? "drop" : "wait", word: ended ? "Not reached" : "Waiting" }, c.label);
  else if (v.lint === "fail") push("lint", { kind: "fail", word: "Failed" }, `errors${of}`);
  else push("lint", { kind: "pass", word: "Clean" }, sheet ? `${sheet.lint.errors} errors, ${sheet.lint.warnings} warnings${of}` : `no errors${of}`);

  if (!c || !v || v.lint === "fail") push("simulate", c && busy ? { kind: "run", word: "Running" } : { kind: ended ? "drop" : "wait", word: ended ? "Not reached" : "Waiting" }, c ? c.label : null);
  else if (v.total > 0 && v.passed === v.total) push("simulate", { kind: "pass", word: "Passed" }, `${v.passed} of ${v.total} tests${of}`);
  else push("simulate", { kind: "fail", word: "Failed" }, `${v.passed} of ${v.total} tests${of}`);

  if (c?.ppa) push("synth", { kind: "done", word: "Done" }, c.ppa.cellCount !== null ? `${c.ppa.cellCount.toLocaleString("en-US")} cells${of}` : c.label);
  else if (c?.status === "synthesizing" && !ended) push("synth", { kind: "run", word: "Running" }, c.label);
  else if (c && (c.status === "fail" || c.status === "rejected")) push("synth", { kind: "drop", word: "Not measured" }, `${c.label} did not pass the tests`);
  else push("synth", { kind: ended ? "drop" : "wait", word: ended ? "Not measured" : "Waiting" }, c ? c.label : null);

  const tried = Math.max(0, written - 1);
  push("improve", fromStage(run.stages.evolve.status, ended), tried > 0 ? `${tried} ${tried === 1 ? "version" : "versions"} tried after ${baseLabel}` : null);

  // Equivalence: the winner against v1, and against the human design when the run measured one.
  const human = ctx.human?.eqy.status === "proven" ? " and the human design" : "";
  if (winner?.eqy) push("equivalence", proofWord(winner.eqy.status), `${winner.label} vs ${baseLabel}${winner.eqy.status === "proven" ? human : ""}`);
  else if (run.stages.prove.status === "skipped") push("equivalence", { kind: "drop", word: "Not needed" }, run.stages.prove.detail ?? `${baseLabel} is the winner`);
  else push("equivalence", fromStage(run.stages.prove.status, ended), null);

  const so = ctx.signoff;
  switch (so.state) {
    case "ok":
      push("signoff", signoffClean(so.sheet) ? { kind: "pass", word: "Signed off" } : { kind: "fail", word: "See the sheet" }, so.label);
      break;
    case "running":
      push("signoff", { kind: "run", word: "Running" }, "after the result");
      break;
    case "waiting":
      push("signoff", { kind: "wait", word: "Waiting" }, "after the winner");
      break;
    case "bad":
      push("signoff", { kind: "fail", word: "Could not be read" }, null);
      break;
    case "none":
      push("signoff", { kind: "drop", word: "Not run" }, null);
      break;
    case "unsupported":
      push("signoff", { kind: "drop", word: "Not on this server" }, null);
      break;
  }

  steps.push({ id: "assertions", title: RAIL_TITLES.assertions, kind: "wait", word: "Not run by Verity yet", detail: null, off: true });
  steps.push({ id: "coverage", title: RAIL_TITLES.coverage, kind: "wait", word: "Not run by Verity yet", detail: null, off: true });
  return steps;
}
