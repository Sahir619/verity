// The tool steps and the panel tab in a reply from POST /threads/{id}/messages (the chip engineer agent). Kept apart from
// lib/sources/live.ts so a thread saved in this browser can be checked without importing the module that talks to the agent.

import { z } from "zod";

/** What the agent did before it answered, one line per tool that code ran. `tool` is a name the server owns ("research", "run_facts"); the line shows `label`. */
export const ToolStepSchema = z.object({ tool: z.string(), label: z.string(), ok: z.boolean() });
export type ToolStep = z.infer<typeof ToolStepSchema>;

/** Steps shown above one reply. More than this are not drawn: the line stays a glance, not a log. */
const MAX_TOOL_STEPS = 8;

/** The steps that can be read, one by one: a step this bench cannot read is dropped on its own, never the reply. */
export function readToolSteps(x: unknown): ToolStep[] {
  if (!Array.isArray(x)) return [];
  const steps = x.flatMap((s) => {
    const p = ToolStepSchema.safeParse(s);
    return p.success && p.data.label.trim() !== "" ? [p.data] : [];
  });
  return steps.slice(0, MAX_TOOL_STEPS);
}

export const OPEN_TABS = ["result", "versions", "verilog", "architecture", "signoff"] as const;
export type OpenTabId = (typeof OPEN_TABS)[number];

/** A panel tab the agent asks to show: a run of this thread, and a version ("v2") when the tab is about one. */
export const OpenTabSchema = z.object({ runId: z.string().min(1), tab: z.enum(OPEN_TABS), version: z.string().optional() });
export type OpenTab = z.infer<typeof OpenTabSchema>;

/** The tab to open, or nothing when the value cannot be read: a tab this bench does not have never fails the reply. */
export function readOpenTab(x: unknown): OpenTab | undefined {
  const p = OpenTabSchema.safeParse(x);
  return p.success ? p.data : undefined;
}
