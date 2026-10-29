import { z } from "zod";

export const CpuKindSchema = z.object({ id: z.string().min(1), name: z.string(), summary: z.string(), buildable: z.boolean() });
export const CpuKindsSchema = z.array(CpuKindSchema);
export type CpuKind = z.infer<typeof CpuKindSchema>;

// Mirrors CPU_KINDS. The live choice card uses the code-supplied parse reply, never model-generated kinds.
export const cpuKinds: CpuKind[] = [
  { id: "rv32i_micro", name: "RV32I microcontroller core, multicycle, no pipeline", summary: "RV32I base instructions, seven blocks and an integrated core. No caches or MMU. Same class as PicoRV32, a small RISC-V core used on FPGAs.", buildable: true },
  { id: "rv32im", name: "RV32IM", summary: "Adds multiply and divide in hardware.", buildable: false },
  { id: "rv32imc", name: "RV32IMC", summary: "Adds compressed instructions. The ESP32-C3 Wi-Fi chip uses an RV32IMC core.", buildable: false },
  { id: "rv32i_pipeline", name: "2-stage pipelined RV32I", summary: "Fetches the next instruction while one executes. Ibex, the core in OpenTitan, uses this design.", buildable: false },
  { id: "rv32i_cached", name: "RV32I with a cache", summary: "A small cache in front of slow memory: the idea behind the Ryzen 5800X3D's stacked cache, at small scale.", buildable: false },
  { id: "application_class", name: "Application-class cores", summary: "Cores with caches and an MMU that run Linux, like Rocket or SiFive U74. Desktop chips such as AMD Ryzen are far beyond this.", buildable: false },
];

// Spec ids of upgrade jobs map to the choice-card kind they build.
const SPEC_KINDS: Record<string, string> = { rv32i_pipe2: "rv32i_pipeline", rv32i_cached: "rv32i_cached" };
export const cpuSpecKind = (specId: string): CpuKind | undefined => cpuKinds.find((k) => k.id === (SPEC_KINDS[specId] ?? specId));
/** Kinds that a passed CPU v1 builds as an upgrade from its board, not from the choice card. */
export const UPGRADE_KIND_IDS: ReadonlySet<string> = new Set(Object.values(SPEC_KINDS));
export const cpuSpecName = (specId: string): string => cpuSpecKind(specId)?.name ?? specId;
export const UPGRADE_LABELS: Record<string, string> = { pipeline_2: "2-stage pipeline", cache_16: "16-word cache" };

export const CPU_BLOCKS = [
  { blockId: "imm", packId: "rv32i_imm", title: "Immediate generator" },
  { blockId: "dec", packId: "rv32i_dec", title: "Instruction decoder" },
  { blockId: "alu", packId: "rv32i_alu", title: "Arithmetic and logic" },
  { blockId: "br", packId: "rv32i_br", title: "Branch comparator" },
  { blockId: "lsu", packId: "rv32i_lsu", title: "Load/store unit" },
  { blockId: "rf", packId: "rv32i_rf", title: "Register file" },
  { blockId: "ctl", packId: "rv32i_ctl", title: "Control state machine" },
] as const;

export const CpuGoalSchema = z.enum(["area", "speed", "power"]);
export type CpuGoal = z.infer<typeof CpuGoalSchema>;

// RunSummaryItem and its nested summaries, field for field with contracts/api.py.
export const CpuRunSummarySchema = z.object({
  id: z.string().min(1), presetId: z.string(),
  params: z.record(z.string(), z.union([z.number().int(), z.boolean(), z.string()])).nullable().default(null),
  title: z.string(), status: z.string(), finishedAt: z.string().nullable().default(null),
  winner: z.object({ label: z.string(), areaUm2: z.number().nullable().default(null), clockPeriodNs: z.number().nullable().default(null) }).nullable().default(null),
  outcome: z.object({
    areaVsV1: z.number().nullable().default(null), clockVsV1: z.number().nullable().default(null), powerVsV1: z.number().nullable().default(null),
    versions: z.number().int().nonnegative(), usd: z.number().nonnegative(),
    signoff: z.object({ seedsPassed: z.number().int().nonnegative(), seedsRun: z.number().int().nonnegative() }).optional(),
  }).optional(),
});

// What a winner's sign-off sheet and sim result say, read by code on the server. Null where the run did not report it.
// Defaults keep an older server's job view readable.
const count = z.number().int().nonnegative().nullable().default(null);
export const CpuEvidenceSchema = z.object({
  eqyVsV1: z.enum(["proven", "inconclusive", "not_equivalent", "not_run", "v1_is_winner"]).nullable().default(null),
  testsPassed: count, testsTotal: count, seedsPassed: count, seedsRun: count,
  mutantsKilled: count, mutantsTotal: count, repairs: count,
  techniques: z.array(z.string()).nullable().default(null),
  // riscv-formal on a core. rvfiTimeouts: checks not proven within the time limit. rvfiFailed: names of failed checks.
  rvfiPassed: count, rvfiTotal: count, rvfiTimeouts: count, rvfiFailed: z.array(z.string()).nullable().default(null),
});
export type CpuEvidence = z.infer<typeof CpuEvidenceSchema>;
const noEvidence = (): CpuEvidence => CpuEvidenceSchema.parse({});

export const CpuTotalsSchema = z.object({
  areaUm2: z.number().nullable().default(null), clockNs: z.number().nullable().default(null), powerMw: z.number().nullable().default(null),
});
export type CpuTotals = z.infer<typeof CpuTotalsSchema>;

const measured = z.number().finite().nonnegative();
const nullableMeasured = measured.nullable();
export const CpuProfileSchema = z.object({
  cpi: nullableMeasured,
  cyclesByStall: z.object({ busy: measured.int(), fetch: measured.int(), data: measured.int() }),
  bench: z.array(z.object({
    name: z.string(), instructions: measured.int(), cycles: measured.int(), cpi: nullableMeasured, passed: z.boolean(),
    cyclesByStall: z.object({ busy: measured.int(), fetch: measured.int(), data: measured.int() }).nullable().default(null),
    selfCheck: z.number().int().nullable().default(null), message: z.string().nullable().default(null),
  })),
  benchTimeUs: nullableMeasured,
  areaByBlockUm2: z.record(z.string(), measured),
  criticalPathBlocks: z.array(z.string()).nullable(),
  clockNs: nullableMeasured,
  // Memory wait cycles the benchmarks ran with: 0 fast memory, 3 flash-like memory.
  waitCycles: z.number().int().nonnegative().default(0),
});
export type CpuProfile = z.infer<typeof CpuProfileSchema>;

// CPU v1 is the first core. Later versions swap in one improved block winner and rerun the core.
export const CpuVersionSchema = z.object({
  version: z.union([z.literal(1), z.literal(2), z.literal(3)]),
  coreRunId: z.string().nullable().default(null), changedBlock: z.string().nullable().default(null), blockRunId: z.string().nullable().default(null),
  totals: CpuTotalsSchema, coreSummary: CpuRunSummarySchema.nullable().default(null),
  coreEvidence: CpuEvidenceSchema.default(noEvidence), blockEvidence: CpuEvidenceSchema.nullable().default(null),
  // Benchmarks on the core winner: profile at fast memory, flashProfile at flash-like memory. Null means not measured.
  profile: CpuProfileSchema.nullable().default(null), flashProfile: CpuProfileSchema.nullable().default(null), profileNote: z.string().nullable().default(null),
  rvfiPassed: count, rvfiTotal: count, rvfiTimeouts: count, rvfiFailed: z.array(z.string()).nullable().default(null),
});
export type CpuVersion = z.infer<typeof CpuVersionSchema>;

const delta = z.number().finite().nullable().default(null);
export const BenchDeltaSchema = z.object({
  name: z.string(), parentCycles: count, cycles: count, cyclesDeltaPct: delta, parentCpi: delta, cpi: delta,
});
export const ProfileComparisonSchema = z.object({
  waitCycles: z.number().int().nonnegative(),
  parent: CpuProfileSchema.nullable().default(null), child: CpuProfileSchema.nullable().default(null),
  cpiDeltaPct: delta, benchTimeRatio: delta, bench: z.array(BenchDeltaSchema).default([]),
});
export type ProfileComparison = z.infer<typeof ProfileComparisonSchema>;
// An upgrade job against its parent's CPU v1, computed by code from the two measured profiles.
export const CpuComparisonSchema = z.object({
  parentAreaUm2: delta, areaUm2: delta, areaDeltaPct: delta,
  parentClockNs: delta, clockNs: delta, clockDeltaPct: delta,
  fast: ProfileComparisonSchema.nullable().default(null), flash: ProfileComparisonSchema.nullable().default(null),
});
export type CpuComparison = z.infer<typeof CpuComparisonSchema>;

export const CpuJobViewSchema = z.object({
  id: z.string().min(1), specId: z.string().min(1), goal: CpuGoalSchema,
  status: z.enum(["running", "integrating", "improving", "passed", "failed", "stopped"]),
  createdAt: z.string(), finishedAt: z.string().nullable(), spentUsd: z.number().nonnegative(), message: z.string().nullable(),
  blocks: z.array(z.object({
    blockId: z.string(), packId: z.string(), title: z.string(), status: z.enum(["waiting", "running", "passed", "failed"]),
    runId: z.string().nullable(), attempts: z.number().int().nonnegative(), summary: CpuRunSummarySchema.nullable(),
    evidence: CpuEvidenceSchema.default(noEvidence),
    // ran or reused: the block read this research. off, skipped or failed: it ran without research.
    research: z.enum(["pending", "ran", "reused", "skipped", "failed", "off"]).default("pending"),
    researchId: z.string().nullable().default(null),
    // "v1" when the run's winner was not proven equal to its own v1, so the CPU uses v1 instead.
    usedVersion: z.string().nullable().default(null),
    // An upgrade job took this block from its parent: no run was made, runId and evidence are the parent's.
    reused: z.boolean().default(false),
  })).min(CPU_BLOCKS.length),
  core: z.object({
    packId: z.string().nullable(), runId: z.string().nullable(), status: z.enum(["locked", "running", "passed", "failed"]),
    summary: CpuRunSummarySchema.nullable(), evidence: CpuEvidenceSchema.default(noEvidence),
  }),
  versions: z.array(CpuVersionSchema).default([]),
  parentJobId: z.string().nullable().default(null), upgradeId: z.string().nullable().default(null), comparison: CpuComparisonSchema.nullable().default(null),
  retryOf: z.string().nullable().default(null), // a core retry: the failed job whose seven passed blocks this job reuses
  threadId: z.string().nullable().default(null), // the chat the job was started from (owner only)
  retryJobId: z.string().nullable().default(null), // the newest core retry started from this job, null when none
});
export type CpuJobView = z.infer<typeof CpuJobViewSchema>;
// What the agent proposes to start in a chat. Every field but `text` is written by code; the job starts only after Confirm.
export const CpuActionSchema = z.object({
  type: z.enum(["new_job", "retry_core", "upgrade"]), goal: CpuGoalSchema,
  jobId: z.string().nullable().default(null), upgradeId: z.enum(["pipeline_2", "cache_16"]).nullable().default(null),
  title: z.string(), summary: z.string(), maxUsd: z.number().nonnegative(),
});
export type CpuAction = z.infer<typeof CpuActionSchema>;
export const cpuJobActive = (job: CpuJobView): boolean => job.status === "running" || job.status === "integrating" || job.status === "improving";

export const UpgradeOptionSchema = z.object({
  id: z.enum(["pipeline_2", "cache_16", "fetch_overlap", "decode_merge", "rv32e", "ext_m", "rf_banked_read"]),
  label: z.string(), targets: z.array(z.string()),
  prediction: z.object({
    kind: z.literal("estimate"), cpi: nullableMeasured, cpiDelta: z.number().finite().nullable(),
    areaDeltaPct: z.number().finite(), clockDeltaPct: z.number().finite(),
    areaUm2: nullableMeasured, clockNs: nullableMeasured, timeRatio: nullableMeasured, powerRatio: measured,
    formula: z.string(), assumptions: z.string(),
    // The memory wait the CPI numbers are relative to: 0 fast memory, 3 flash-like memory (cache_16).
    basisWaitCycles: z.number().int().nonnegative().default(0),
  }),
  sources: z.array(z.object({ title: z.string(), url: z.url().refine((url) => /^https?:\/\//i.test(url)) })),
  needs: z.array(z.string()), blocks: z.array(z.string()), contractParts: z.array(z.string()),
  buildable: z.boolean(), recommended: z.boolean(), reason: z.string(),
});
export const UpgradesResponseSchema = z.object({ profile: CpuProfileSchema.nullable(), flashProfile: CpuProfileSchema.nullable().default(null), options: z.array(UpgradeOptionSchema), message: z.string() });
export type UpgradesResponse = z.infer<typeof UpgradesResponseSchema>;
