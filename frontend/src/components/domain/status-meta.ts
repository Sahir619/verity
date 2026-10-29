import { Check, Lock, Wrench, X, type LucideIcon } from "lucide-react";
import type { CandidateStatus, StageStatus } from "@/lib/contracts/types";
import type { VisualStatus } from "@/lib/contracts/view-types";

export type Tone = "pass" | "fail" | "info" | "warn" | "neutral";

export type StatusMeta = { visual: VisualStatus; word: string; icon: LucideIcon | null; tone: Tone };

/** Tinted fill plus text color per tone. Always paired with a square and a word, never color alone. */
export const TONE_CLASS: Record<Tone, string> = {
  pass: "bg-pass-muted text-pass",
  fail: "bg-fail-muted text-fail",
  info: "bg-info-muted text-info",
  warn: "bg-warn-muted text-warn",
  neutral: "bg-surface-2 text-fg-muted",
};

/** Maps every candidate, stage and lock status to its square, word, icon and tone. */
export function statusMeta(status: CandidateStatus | StageStatus | "locked"): StatusMeta {
  switch (status) {
    case "drafting":
      return { visual: "running", word: "Writing RTL", icon: null, tone: "info" };
    case "linting":
      return { visual: "running", word: "Linting", icon: null, tone: "info" };
    case "simulating":
      return { visual: "running", word: "Simulating", icon: null, tone: "info" };
    case "fail":
      return { visual: "fail", word: "FAIL", icon: X, tone: "fail" };
    case "fixing":
      return { visual: "fail", word: "Fixing", icon: Wrench, tone: "fail" };
    case "pass":
      return { visual: "pass", word: "PASS", icon: Check, tone: "pass" };
    case "synthesizing":
      return { visual: "running", word: "Measuring", icon: null, tone: "info" };
    case "measured":
      return { visual: "pass", word: "Measured", icon: Check, tone: "pass" };
    case "rejected":
      return { visual: "rejected", word: "Rejected", icon: X, tone: "neutral" };
    case "pending":
      return { visual: "idle", word: "Pending", icon: null, tone: "neutral" };
    case "active":
      return { visual: "running", word: "Running", icon: null, tone: "info" };
    case "done":
      return { visual: "done", word: "Done", icon: Check, tone: "pass" };
    case "failed":
      return { visual: "fail", word: "Failed", icon: X, tone: "fail" };
    case "skipped":
      return { visual: "idle", word: "Skipped", icon: null, tone: "neutral" };
    case "locked":
      return { visual: "locked", word: "Locked", icon: Lock, tone: "neutral" };
  }
}
