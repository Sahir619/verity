import type { Candidate, CandidateStatus, CreatedBy, Credits, NodeKind, PlanBlock, PlanDiff,
  PPA, Priority, RunMode, RunPhase, Spec, StageId, StageStatus } from "./types";

export type VisualStatus = "idle" | "queued" | "running" | "pass" | "fail" | "warn" | "rejected" | "locked" | "done";
export type PlaybackStatus = "loading" | "ready" | "playing" | "paused" | "ended" | "error";
export type LiveConnection = "idle" | "connecting" | "open" | "retrying" | "closed" | "error";
export type InspectorTab = "rtl" | "diff" | "tests" | "ppa";
export type Selection = { kind: NodeKind; id: string };
export type StageView = { id: StageId; label: string; status: StageStatus; detail?: string; hint: string };
export type CandidateListItem = { id: string; label: string; status: CandidateStatus; createdBy: CreatedBy;
  fitness: number | null; tags: string[]; hidden?: boolean };
export type Preset = { id: string; title: string; oneLine: string; spec: Spec; approxMinutes: number; recommended?: boolean };
export type SteerRequest =
  | { kind: "pin"; blockId: string; pin: string }
  | { kind: "toggle"; blockId: string; variantId: string; value: string }
  | { kind: "gate"; action: "build_now" | "hold" | "resume" }
  | { kind: "autopilot"; on: boolean }
  | { kind: "winner_override"; candidateId: string };
export type SelectedEntity =
  | { kind: "spec"; spec: Spec }
  | { kind: "block"; block: PlanBlock; lastDiff: PlanDiff | null; planRevision: number }
  | { kind: "golden"; testCount: number; files: string[]; source: string | null }
  | { kind: "candidate"; candidate: Candidate; parentLabel: string | null; baseline: PPA | null;
      isWinner: boolean; winnerBy: "ai" | "user" | null; aiWinnerLabel: string | null };
export type RunSummary = {
  title: string; mode: RunMode; sample: boolean; phase: RunPhase;
  winnerId: string | null; aiWinnerId: string | null; winnerBy: "ai" | "user" | null;
  versionsTried: number; rejected: number; rounds: number; fixes: number;
  humanInputItems: { label: string; local?: boolean }[];
  credits: Credits; recordedUsd: number | null; priority: Priority | null;
  candidates: CandidateListItem[];
};
export type LiveHealth = { status: "checking" | "ready" | "not_configured" | "budget_exhausted" | "busy" | "sandbox_down" | "error"; capUsd: number | null; authRequired?: boolean;
  /** Bench capabilities the agent reports in GET /health ("params", "parse", "human_ref"); empty when it reports none. */
  features?: string[] };
