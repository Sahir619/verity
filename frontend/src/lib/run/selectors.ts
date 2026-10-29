import { STAGES } from "@/lib/contracts/stages";
import type { HumanInput, PlanBlock, Priority, RunState } from "@/lib/contracts/types";
import type {
  CandidateListItem,
  RunSummary,
  SelectedEntity,
  Selection,
  StageView,
} from "@/lib/contracts/view-types";

export function selectStageViews(run: RunState): StageView[] {
  return STAGES.map((s) => ({
    id: s.id,
    label: s.label,
    hint: s.hint,
    status: run.stages[s.id].status,
    detail: run.stages[s.id].detail,
  }));
}

const GOAL_TEXT: Record<Priority, string> = { area: "smallest area", speed: "fastest clock", power: "lowest power" };

function shortBlockName(block: PlanBlock | undefined, fallback: string): string {
  if (!block) return fallback;
  return block.name.replace(/\s*\(.*\)\s*$/, "");
}

function humanInputLabel(run: RunState, input: HumanInput): { label: string; local?: boolean } {
  switch (input.kind) {
    case "spec": {
      const title = run.spec?.title ?? run.title;
      return { label: `The spec (${title}, ${GOAL_TEXT[input.priority]})` };
    }
    case "pin": {
      const block = run.plan.blocks.find((b) => b.id === input.blockId);
      return { label: `Pinned "${input.pin}" on ${shortBlockName(block, input.blockId)}` };
    }
    case "toggle": {
      const block = run.plan.blocks.find((b) => b.id === input.blockId);
      const variant = block?.variants.find((v) => v.id === input.variantId);
      const what = variant ? variant.label.toLowerCase() : input.variantId;
      return { label: `${shortBlockName(block, input.blockId)} ${what} set to ${input.value}` };
    }
    case "winner_override": {
      const label = run.candidates[input.candidateId]?.label ?? input.candidateId;
      return { label: `Winner set to ${label}`, local: input.local };
    }
    case "stop":
      return { label: "Stopped the run" };
  }
}

/**
 * Pure derivation for Results and the Studio list. Pass the ids the visibility budget hid
 * so the version list can mark them (the list still includes every version).
 */
export function selectRunSummary(run: RunState, hiddenIds?: ReadonlySet<string>): RunSummary {
  const all = run.candidateOrder.map((id) => run.candidates[id]).filter(Boolean);
  const candidates: CandidateListItem[] = all.map((c) => {
    const tags: string[] = [];
    if (c.isBaseline) tags.push("baseline");
    if (c.isElite) tags.push("elite");
    if (c.id === run.winnerId) tags.push("winner");
    if (c.status === "rejected") tags.push("rejected");
    if (c.fixCount > 0) tags.push("fixed");
    return {
      id: c.id,
      label: c.label,
      status: c.status,
      createdBy: c.createdBy,
      fitness: c.fitness ? c.fitness.score : null,
      tags,
      hidden: hiddenIds?.has(c.id) ? true : undefined,
    };
  });
  const isReplay = run.mode === "replay";
  return {
    title: run.title,
    mode: run.mode,
    sample: run.sample,
    phase: run.phase,
    winnerId: run.winnerId,
    aiWinnerId: run.aiWinnerId,
    winnerBy: run.winnerBy,
    versionsTried: all.length,
    rejected: all.filter((c) => c.status === "rejected").length,
    rounds: run.round,
    fixes: all.reduce((sum, c) => sum + c.fixCount, 0),
    humanInputItems: run.humanInputs.map((h) => humanInputLabel(run, h)),
    // A replay costs nothing; the recording's own spend is shown separately.
    credits: isReplay ? { usedUsd: 0, capUsd: null } : run.credits,
    recordedUsd: isReplay && run.credits.usedUsd > 0 ? run.credits.usedUsd : null,
    priority: run.spec?.priority ?? null,
    candidates,
  };
}

/** Resolves the UI selection against the run. Returns null when the selected thing is not (yet) in the run. */
export function selectEntity(run: RunState, selection: Selection | null): SelectedEntity | null {
  if (!selection) return null;
  switch (selection.kind) {
    case "spec":
      return run.spec ? { kind: "spec", spec: run.spec } : null;
    case "block": {
      const block = run.plan.blocks.find((b) => b.id === selection.id);
      return block ? { kind: "block", block, lastDiff: run.plan.lastDiff, planRevision: run.plan.revision } : null;
    }
    case "golden": {
      const testFiles = run.artifacts.filter((a) => a.path.startsWith("tests/"));
      const model = run.artifacts.find((a) => a.path === "tests/golden_model.py");
      return {
        kind: "golden",
        testCount: run.spec?.testCount ?? 0,
        files: testFiles.map((a) => a.path),
        source: model ? model.content : null,
      };
    }
    case "candidate": {
      const candidate = run.candidates[selection.id];
      if (!candidate) return null;
      const parent = candidate.parentId ? run.candidates[candidate.parentId] : undefined;
      const baseline = run.baselineId ? run.candidates[run.baselineId] : undefined;
      const aiWinner = run.aiWinnerId ? run.candidates[run.aiWinnerId] : undefined;
      const isWinner = run.winnerId === candidate.id;
      return {
        kind: "candidate",
        candidate,
        parentLabel: parent ? parent.label : null,
        baseline: baseline ? baseline.ppa : null,
        isWinner,
        winnerBy: isWinner ? run.winnerBy : null,
        aiWinnerLabel: run.winnerBy === "user" && aiWinner ? aiWinner.label : null,
      };
    }
  }
}
