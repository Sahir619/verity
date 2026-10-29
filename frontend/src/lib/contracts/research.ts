// The fact check and research on a first message, as POST /parse sends them. Kept apart from lib/sources/live.ts so the
// "Your runs" list can check a saved copy without importing the module that talks to the agent.

import { z } from "zod";

export const VERDICTS = [
  "buildable_now",
  "buildable_with_changes",
  "not_buildable_here",
  "not_a_chip",
  "could_not_check",
  "custom_block",
  "cpu_choice",
] as const;
export type Verdict = (typeof VERDICTS)[number];

const SourceSchema = z.object({
  n: z.number().int(),
  url: z.string(),
  title: z.string(),
  domain: z.string(),
  tier: z.enum(["A", "B", "C", "P"]),
  date: z.string().nullable().optional(),
  // A search service this bench does not know (a new provider) reads as absent; it never drops the source.
  endpoint: z.enum(["search", "scholar", "patents"]).optional().catch(undefined),
});
export const ResearchSchema = z.object({
  id: z.string().min(1),
  status: z.enum(["ok", "partial", "failed"]),
  reused: z.boolean().default(false),
  createdAt: z.string().default(""),
  queries: z.array(z.string()).default([]),
  sentences: z.array(z.object({ text: z.string(), sourceIds: z.array(z.number().int()).default([]) })).default([]),
  sources: z.array(SourceSchema).default([]),
  techniques: z.array(z.object({ id: z.string(), label: z.string() })).default([]),
  doneBefore: z.boolean().default(false),
  pastRuns: z.number().default(0),
  usd: z.number().default(0),
  serperCredits: z.number().default(0),
  // Searches the other search service ran. An older server omits it (no such search), so it reads as 0.
  tavilyCredits: z.number().default(0),
});
export type Research = z.infer<typeof ResearchSchema>;
export type ResearchSource = Research["sources"][number];
