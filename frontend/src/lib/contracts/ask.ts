// A walkthrough in an answer from POST /runs/{id}/ask: the steps of the run, written by the agent's code, each tied to a
// step of the Architecture tab and, when it applies, to one version and some parts of its circuit. Kept apart from
// lib/sources/live.ts so the saved conversation can be checked without importing the module that talks to the agent.

import { z } from "zod";

export const WALK_STEP_IDS = ["spec", "plan", "rtl", "lint", "simulate", "synth", "improve", "equivalence", "signoff"] as const;
export type WalkStepId = (typeof WALK_STEP_IDS)[number];

export const WalkStepSchema = z.object({
  stepId: z.enum(WALK_STEP_IDS),
  title: z.string(),
  text: z.string(),
  candidateId: z.string().optional(),
  nodeIds: z.array(z.string()).optional(),
});
export type WalkStep = z.infer<typeof WalkStepSchema>;

/** The steps that can be read, one by one: a step this bench does not know is dropped on its own, never the answer. */
export function readSteps(x: unknown): WalkStep[] | undefined {
  if (!Array.isArray(x)) return undefined;
  const steps = x.flatMap((s) => {
    const p = WalkStepSchema.safeParse(s);
    return p.success ? [p.data] : [];
  });
  return steps.length > 0 ? steps : undefined;
}
