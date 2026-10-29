// Zod mirrors of every type in ./types.ts.
// Constraints (ARCHITECTURE sections 5 and 13): import only from "zod", no path aliases,
// erasable TypeScript only (no enum, no namespace), no z.record. The Node fixture validator loads this file directly.
import { z } from "zod";

// ---------- enums ----------
export const PrioritySchema = z.enum(["area", "speed", "power"]);
export const OperatorSchema = z.enum(["FIX", "SIMPLIFY", "OPTIMIZE", "EXPLORE"]);
export const CreatedBySchema = z.enum(["INIT", "SIMPLIFY", "OPTIMIZE", "EXPLORE"]);
export const StageIdSchema = z.enum(["spec", "plan", "lock", "rtl", "lint", "sim", "fix", "synth", "evolve", "prove"]);
export const StageStatusSchema = z.enum(["pending", "active", "done", "failed", "skipped"]);
export const CandidateStatusSchema = z.enum([
  "drafting", "linting", "simulating", "fail", "fixing", "pass", "synthesizing", "measured", "rejected",
]);
export const RunModeSchema = z.enum(["replay", "live"]);
export const RunPhaseSchema = z.enum(["idle", "starting", "running", "done", "error", "cap_reached", "stopped"]);
export const ZoneIdSchema = z.enum(["plan", "tree"]);
export const NodeKindSchema = z.enum(["spec", "block", "golden", "candidate"]);
export const EdgeKindSchema = z.enum(["plan", "lineage", "checks", "proof"]);
export const ActorSchema = z.enum(["ai", "user", "system"]);

// ---------- spec and plan ----------
export const PortSchema = z.object({
  name: z.string(),
  dir: z.enum(["in", "out"]),
  width: z.number(),
  count: z.number().optional(),
});
export const SpecSchema = z.object({
  presetId: z.string(),
  title: z.string(),
  text: z.string(),
  ports: z.array(PortSchema),
  priority: PrioritySchema,
  testCount: z.number(),
  maxLines: z.number(),
});
export const VariantSchema = z.object({
  id: z.string(),
  label: z.string(),
  options: z.array(z.string()),
  value: z.string(),
});
export const PlanBlockSchema = z.object({
  id: z.string(),
  name: z.string(),
  reason: z.string(),
  pinOptions: z.array(z.string()),
  pins: z.array(z.string()),
  variants: z.array(VariantSchema),
});
export const PlanDiffSchema = z.object({
  added: z.array(z.string()),
  changed: z.array(z.string()),
  removed: z.array(z.string()),
});

// ---------- verification ----------
export const TestCaseSchema = z.object({
  name: z.string(),
  inputs: z.string(),
  expected: z.string(),
  got: z.string(),
  pass: z.boolean(),
  cycle: z.number().optional(),
});
export const VerificationSchema = z.object({
  lint: z.enum(["pass", "fail"]),
  lintLog: z.string().optional(),
  passed: z.number(),
  total: z.number(),
  failures: z.array(TestCaseSchema),
  diagnosis: z.string().optional(),
  tool: z.string(),
  durationMs: z.number(),
});
export const AttemptSchema = z.object({
  n: z.number(),
  by: z.enum(["INIT", "SIMPLIFY", "OPTIMIZE", "EXPLORE", "FIX"]),
  reason: z.string(),
  rtl: z.string(),
  verification: VerificationSchema,
});

// ---------- PPA, fitness, proof ----------
export const PPASchema = z.object({
  areaUm2: z.number().nullable(),
  cellCount: z.number().nullable(),
  clockPeriodNs: z.number().nullable(),
  powerMw: z.number().nullable(),
  tools: z.array(z.string()),
  pdk: z.string(),
});
export const FitnessWeightsSchema = z.object({ area: z.number(), timing: z.number(), power: z.number() });
export const FitnessSchema = z.object({
  score: z.number(),
  priority: PrioritySchema,
  weights: FitnessWeightsSchema,
  gains: z.object({
    area: z.number().nullable(),
    timing: z.number().nullable(),
    power: z.number().nullable(),
  }),
  baselineId: z.string(),
});
export const EquivalenceSchema = z.object({
  status: z.enum(["proven", "not_equivalent", "inconclusive", "not_run"]),
  againstId: z.string(),
  tool: z.literal("yosys-eqy"),
  log: z.string().optional(),
});

// ---------- candidate ----------
export const CandidateSchema = z.object({
  id: z.string(),
  label: z.string(),
  round: z.number(),
  parentId: z.string().nullable(),
  createdBy: CreatedBySchema,
  reason: z.string(),
  rtl: z.string(),
  attempts: z.array(AttemptSchema),
  verification: VerificationSchema.nullable(),
  ppa: PPASchema.nullable(),
  fitness: FitnessSchema.nullable(),
  status: CandidateStatusSchema,
  fixCount: z.number(),
  isBaseline: z.boolean(),
  isElite: z.boolean(),
  eqy: EquivalenceSchema.optional(),
});

// ---------- human inputs and credits ----------
export const HumanInputSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("spec"), tMs: z.number(), presetId: z.string(), priority: PrioritySchema }),
  z.object({ kind: z.literal("pin"), tMs: z.number(), blockId: z.string(), pin: z.string() }),
  z.object({
    kind: z.literal("toggle"),
    tMs: z.number(),
    blockId: z.string(),
    variantId: z.string(),
    value: z.string(),
  }),
  z.object({
    kind: z.literal("winner_override"),
    tMs: z.number(),
    candidateId: z.string(),
    local: z.boolean().optional(),
  }),
  z.object({ kind: z.literal("stop"), tMs: z.number() }),
]);
export const CreditsSchema = z.object({ usedUsd: z.number(), capUsd: z.number().nullable() });

// ---------- canvas ops ----------
export const CanvasOpSchema = z.discriminatedUnion("op", [
  z.object({
    op: z.literal("add_node"),
    id: z.string(),
    kind: NodeKindSchema,
    zone: ZoneIdSchema,
    label: z.string(),
    refId: z.string().optional(),
    near: z.string().optional(),
  }),
  z.object({
    op: z.literal("connect"),
    source: z.string(),
    target: z.string(),
    kind: EdgeKindSchema,
    label: z.string().optional(),
  }),
  z.object({ op: z.literal("move_node"), id: z.string(), x: z.number(), y: z.number() }),
  z.object({ op: z.literal("update_status"), id: z.string(), status: z.string(), note: z.string().optional() }),
  z.object({ op: z.literal("remove"), id: z.string() }),
]);

// ---------- the event stream ----------
const eventBase = { seq: z.number(), tMs: z.number() };

export const RunEventSchema = z.discriminatedUnion("t", [
  z.object({ ...eventBase, t: z.literal("run"), phase: RunPhaseSchema, message: z.string().optional() }),
  z.object({
    ...eventBase,
    t: z.literal("stage"),
    stage: StageIdSchema,
    status: StageStatusSchema,
    detail: z.string().optional(),
    round: z.number().optional(),
  }),
  z.object({ ...eventBase, t: z.literal("op"), by: ActorSchema, op: CanvasOpSchema }),
  z.object({ ...eventBase, t: z.literal("spec"), spec: SpecSchema }),
  z.object({
    ...eventBase,
    t: z.literal("plan"),
    revision: z.number(),
    blocks: z.array(PlanBlockSchema),
    diff: PlanDiffSchema.optional(),
    reason: z.string().optional(),
  }),
  z.object({
    ...eventBase,
    t: z.literal("gate"),
    state: z.enum(["open", "held", "closed"]),
    autoContinueMs: z.number(),
  }),
  z.object({ ...eventBase, t: z.literal("candidate"), candidate: CandidateSchema }),
  z.object({
    ...eventBase,
    t: z.literal("round"),
    round: z.number(),
    parents: z.array(z.string()),
    elites: z.array(z.string()).optional(),
  }),
  z.object({
    ...eventBase,
    t: z.literal("winner"),
    candidateId: z.string(),
    by: z.enum(["ai", "user"]),
    aiPickId: z.string(),
  }),
  z.object({ ...eventBase, t: z.literal("human"), input: HumanInputSchema }),
  z.object({ ...eventBase, t: z.literal("caption"), text: z.string(), chapter: z.string().optional() }),
  z.object({ ...eventBase, t: z.literal("credits"), usedUsd: z.number(), capUsd: z.number().nullable() }),
  z.object({ ...eventBase, t: z.literal("artifact"), path: z.string(), content: z.string() }),
  z.object({ ...eventBase, t: z.literal("human_ref"), ppa: PPASchema, eqy: EquivalenceSchema }),
]);

// ---------- recorded log ----------
export const ChapterSchema = z.object({ id: z.string(), label: z.string(), tMs: z.number() });
export const RunLogSchema = z.object({
  format: z.literal("verity.runlog/1"),
  meta: z.object({
    runId: z.string(),
    sample: z.boolean(),
    title: z.string(),
    presetId: z.string(),
    recordedAt: z.string(),
    durationMs: z.number(),
    chapters: z.array(ChapterSchema),
    note: z.string().optional(),
  }),
  events: z.array(RunEventSchema),
});
