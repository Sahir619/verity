// ---------- enums ----------
export type Priority = "area" | "speed" | "power";
export type Operator = "FIX" | "SIMPLIFY" | "OPTIMIZE" | "EXPLORE";
/** What created a candidate. FIX never creates a candidate: it repairs one in place (an Attempt). */
export type CreatedBy = "INIT" | "SIMPLIFY" | "OPTIMIZE" | "EXPLORE";
export type StageId = "spec" | "plan" | "lock" | "rtl" | "lint" | "sim" | "fix" | "synth" | "evolve" | "prove";
export type StageStatus = "pending" | "active" | "done" | "failed" | "skipped";
export type CandidateStatus =
  | "drafting" | "linting" | "simulating" | "fail" | "fixing"
  | "pass" | "synthesizing" | "measured" | "rejected";
export type RunMode = "replay" | "live";
export type RunPhase = "idle" | "starting" | "running" | "done" | "error" | "cap_reached" | "stopped";
export type ZoneId = "plan" | "tree";
export type NodeKind = "spec" | "block" | "golden" | "candidate";
export type EdgeKind = "plan" | "lineage" | "checks" | "proof";
export type Actor = "ai" | "user" | "system";

// ---------- spec and plan ----------
export interface Port { name: string; dir: "in" | "out"; width: number; count?: number }
export interface Spec {
  presetId: string; title: string; text: string; ports: Port[];
  priority: Priority; testCount: number; maxLines: number;
}
export interface Variant { id: string; label: string; options: string[]; value: string }
export interface PlanBlock {
  id: string; name: string; reason: string;
  pinOptions: string[]; pins: string[]; variants: Variant[];
}
export interface PlanDiff { added: string[]; changed: string[]; removed: string[] }

// ---------- verification ----------
export interface TestCase {
  name: string; inputs: string; expected: string; got: string; pass: boolean; cycle?: number;
}
export interface Verification {
  lint: "pass" | "fail"; lintLog?: string;
  passed: number; total: number;
  failures: TestCase[];          // every failing case (expected vs got)
  diagnosis?: string;            // agent's one-paragraph reading of the failures
  tool: string;                  // e.g. "iverilog 12 + cocotb"
  durationMs: number;
}
export interface Attempt {
  n: number;                     // 1-based
  by: CreatedBy | "FIX";         // attempt 1 = creating operator, later attempts = FIX
  reason: string;                // what changed and why
  rtl: string;
  verification: Verification;
}

// ---------- PPA, fitness, proof ----------
export interface PPA {
  areaUm2: number | null; cellCount: number | null;
  clockPeriodNs: number | null; powerMw: number | null;   // null = not measured
  tools: string[];               // e.g. ["Yosys 0.x", "OpenSTA 3.x"]
  pdk: string;                   // e.g. "Nangate45"
}
export interface FitnessWeights { area: number; timing: number; power: number }
export interface Fitness {
  score: number;                 // weighted gain, see section 8.3
  priority: Priority; weights: FitnessWeights;
  gains: { area: number | null; timing: number | null; power: number | null }; // percent, positive = better
  baselineId: string;
}
export interface Equivalence {
  status: "proven" | "not_equivalent" | "inconclusive" | "not_run";
  againstId: string; tool: "yosys-eqy"; log?: string;
}

// ---------- candidate: the REvolution-lite record ----------
export interface Candidate {
  id: string;                    // "c5"
  label: string;                 // "v5"
  round: number;                 // 0 = initial build
  parentId: string | null;
  createdBy: CreatedBy;
  reason: string;                // generation / change reason
  rtl: string;                   // current RTL = last attempt's RTL
  attempts: Attempt[];
  verification: Verification | null;   // = last attempt's verification
  ppa: PPA | null;               // only after PASS + synthesis
  fitness: Fitness | null;       // null for FAIL, rejected, or baseline-less
  status: CandidateStatus;
  fixCount: number;
  isBaseline: boolean; isElite: boolean;
  eqy?: Equivalence;
}

// ---------- human inputs and credits ----------
export type HumanInput =
  | { kind: "spec"; tMs: number; presetId: string; priority: Priority }
  | { kind: "pin"; tMs: number; blockId: string; pin: string }
  | { kind: "toggle"; tMs: number; blockId: string; variantId: string; value: string }
  | { kind: "winner_override"; tMs: number; candidateId: string; local?: boolean }
  | { kind: "stop"; tMs: number };
export interface Credits { usedUsd: number; capUsd: number | null }

// ---------- canvas ops: the closed set the AI may emit ----------
export type CanvasOp =
  | { op: "add_node"; id: string; kind: NodeKind; zone: ZoneId; label: string; refId?: string; near?: string }
  | { op: "connect"; source: string; target: string; kind: EdgeKind; label?: string }
  | { op: "move_node"; id: string; x: number; y: number }
  | { op: "update_status"; id: string; status: string; note?: string }
  | { op: "remove"; id: string };

// ---------- the event stream (live SSE and recorded log share this) ----------
interface EventBase { seq: number; tMs: number }
export type RunEvent = EventBase & (
  | { t: "run"; phase: RunPhase; message?: string }
  | { t: "stage"; stage: StageId; status: StageStatus; detail?: string; round?: number }
  | { t: "op"; by: Actor; op: CanvasOp }
  | { t: "spec"; spec: Spec }
  | { t: "plan"; revision: number; blocks: PlanBlock[]; diff?: PlanDiff; reason?: string }
  | { t: "gate"; state: "open" | "held" | "closed"; autoContinueMs: number }
  | { t: "candidate"; candidate: Candidate }        // upsert the full record
  | { t: "round"; round: number; parents: string[]; elites?: string[] }
  | { t: "winner"; candidateId: string; by: "ai" | "user"; aiPickId: string }
  | { t: "human"; input: HumanInput }
  | { t: "caption"; text: string; chapter?: string }
  | { t: "credits"; usedUsd: number; capUsd: number | null }
  | { t: "artifact"; path: string; content: string }
  // Bench (spec 5.3): the pack's human-written reference.v measured with the same tools, and EQY of the winner against it.
  | { t: "human_ref"; ppa: PPA; eqy: Equivalence }
);

// ---------- recorded log (golden run fixture) ----------
export interface Chapter { id: string; label: string; tMs: number }
export interface RunLog {
  format: "verity.runlog/1";
  meta: {
    runId: string; sample: boolean; title: string; presetId: string;
    recordedAt: string; durationMs: number; chapters: Chapter[]; note?: string;
  };
  events: RunEvent[];            // sorted by seq; tMs non-decreasing
}

// ---------- view-side state (store) ----------
// A type alias, not an interface: @xyflow/react 12.12.0 declares
// Node<NodeData extends Record<string, unknown>>, and interfaces do not satisfy that constraint.
export type NodeData = {
  kind: NodeKind | "zone"; refId: string; label: string; zone: ZoneId;
  status: string; origin: "ai" | "user";
  diff?: "new" | "changed"; diffAtMs?: number; note?: string;
};
export interface RunState {
  runId: string; mode: RunMode; sample: boolean; title: string; phase: RunPhase;
  spec: Spec | null;
  plan: { revision: number; blocks: PlanBlock[]; lastDiff: PlanDiff | null; reason?: string };
  stages: Record<StageId, { status: StageStatus; detail?: string; round?: number }>;
  candidates: Record<string, Candidate>; candidateOrder: string[];
  round: number; elites: string[];
  baselineId: string | null; winnerId: string | null; aiWinnerId: string | null; winnerBy: "ai" | "user" | null;
  gate: { state: "open" | "held" | "closed"; autoContinueMs: number; openedAtMs: number };
  humanInputs: HumanInput[]; credits: Credits;
  caption: { text: string; chapter?: string; tMs: number } | null;
  artifacts: { path: string; content: string }[];
  lastSeq: number; tMs: number;
}
