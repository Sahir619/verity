import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { CandidateStatus, StageStatus } from "@/lib/contracts/types";
import { formatTests } from "@/lib/format";
import { StatusSquare } from "./StatusSquare";
import { statusMeta, TONE_CLASS } from "./status-meta";

export type StatusBadgeProps = {
  status: CandidateStatus | StageStatus | "locked";
  tests?: { passed: number; total: number };
  size?: "sm" | "md";
};

/** Square + icon or word (+ tests). Uppercase only for PASS and FAIL. */
export function StatusBadge({ status, tests, size = "md" }: StatusBadgeProps) {
  const meta = statusMeta(status);
  const Icon = meta.icon;
  const loud = meta.word === "PASS" || meta.word === "FAIL";
  return (
    <Badge
      variant="secondary"
      className={cn(
        "h-auto rounded-sm font-medium",
        size === "sm" ? "gap-1 px-1.5 py-0.5 text-xs" : "gap-1.5 px-2 py-1 text-sm",
        TONE_CLASS[meta.tone],
      )}
    >
      <StatusSquare status={meta.visual} size="sm" />
      {Icon ? <Icon aria-hidden className="size-3.5" /> : null}
      <span className={cn(loud && "tracking-wider")}>{meta.word}</span>
      {tests ? <span className="font-mono">{formatTests(tests.passed, tests.total)}</span> : null}
    </Badge>
  );
}
