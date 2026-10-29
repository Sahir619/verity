import type { ReactNode } from "react";
import { Crown, Star, Wrench, X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { Candidate, PPA, Priority } from "@/lib/contracts/types";
import { formatTests } from "@/lib/format";
import { CandidateTag } from "./CandidateTag";
import { MetricDelta, type MetricDeltaProps } from "./MetricDelta";
import { OperatorBadge } from "./OperatorBadge";
import { StatusBadge } from "./StatusBadge";
import { StatusSquare } from "./StatusSquare";
import { statusMeta } from "./status-meta";
import { formatScore } from "./signed";

export type CandidateCardProps = {
  candidate: Candidate;
  baseline: PPA | null;
  priority: Priority;
  variant: "node" | "row" | "hero";
  isWinner: boolean;
  winnerBy?: "ai" | "user";
  selected?: boolean;
  diffUnseen?: boolean;
  actions?: ReactNode;
  /** Label of the parent version ("v1"). Falls back to the c<n> to v<n> id convention. */
  parentLabel?: string | null;
};

const PRIORITY_METRIC: Record<Priority, MetricDeltaProps["metric"]> = {
  area: "area",
  speed: "clockPeriod",
  power: "power",
};

function priorityValue(ppa: PPA | null, priority: Priority): number | null {
  if (!ppa) return null;
  if (priority === "area") return ppa.areaUm2;
  if (priority === "speed") return ppa.clockPeriodNs;
  return ppa.powerMw;
}

function priorityBaseline(base: PPA | null, priority: Priority): number | null {
  return priorityValue(base, priority);
}

function parentName(candidate: Candidate, parentLabel?: string | null): string | null {
  if (parentLabel) return parentLabel;
  const m = candidate.parentId ? /^c(\d+)$/.exec(candidate.parentId) : null;
  return m ? `v${m[1]}` : candidate.parentId;
}

function lastTests(candidate: Candidate): { passed: number; total: number } | null {
  const v = candidate.verification ?? candidate.attempts.at(-1)?.verification ?? null;
  return v ? { passed: v.passed, total: v.total } : null;
}

/** Status specific line for the node variant. */
function NodeStatusRow({ candidate, baseline, priority }: { candidate: Candidate; baseline: PPA | null; priority: Priority }) {
  const tests = lastTests(candidate);
  const muted = "truncate text-xs text-fg-muted";
  switch (candidate.status) {
    case "drafting":
      return <span className={muted}>Writing RTL</span>;
    case "linting":
      return <span className={muted}>Linting</span>;
    case "simulating":
      return <span className={muted}>{tests ? `Simulating ${tests.total} tests` : "Simulating the locked tests"}</span>;
    case "fail":
      return <StatusBadge status="fail" size="sm" tests={tests ?? undefined} />;
    case "fixing":
      // Keep the failed result on screen while the fix is written: red stays visible until a run passes.
      return (
        <span className="inline-flex items-center gap-2">
          {tests ? <StatusBadge status="fail" size="sm" tests={tests} /> : null}
          <span className="inline-flex items-center gap-1 text-xs whitespace-nowrap text-fail">
            <Wrench aria-hidden className="size-3" />
            Fixing
          </span>
        </span>
      );
    case "pass":
      return <StatusBadge status="pass" size="sm" tests={tests ?? undefined} />;
    case "synthesizing":
      return (
        <span className="inline-flex items-center gap-2">
          <StatusBadge status="pass" size="sm" tests={tests ?? undefined} />
          <span className="text-xs text-info">Measuring</span>
        </span>
      );
    case "rejected":
      return (
        <span className="inline-flex items-center gap-1 truncate text-xs text-fail">
          <X aria-hidden className="size-3 shrink-0" />
          {`Rejected: failed ${candidate.attempts.length} attempts`}
        </span>
      );
    case "measured": {
      const testText = tests ? <span className="font-mono text-xs text-fg-muted">{formatTests(tests.passed, tests.total)}</span> : null;
      if (candidate.isBaseline) {
        return (
          <span className="inline-flex items-center gap-2">
            {testText}
            <CandidateTag tag="baseline" />
          </span>
        );
      }
      return (
        // A measured version already passed every test (its square says so), so the row spends its width on the
        // gain over v1 and the fitness score, spelled out rather than abbreviated.
        <span className="inline-flex items-baseline gap-1.5 whitespace-nowrap">
          <MetricDelta
            metric={PRIORITY_METRIC[priority]}
            value={priorityValue(candidate.ppa, priority)}
            baseline={priorityBaseline(baseline, priority)}
            size="sm"
            showLabel
            showArrow={false}
          />
          {candidate.fitness ? (
            <span className="text-xs text-fg-muted">
              score <span className="font-mono">{formatScore(candidate.fitness.score)}</span>
            </span>
          ) : null}
        </span>
      );
    }
  }
}

function RoleIcons({ candidate, isWinner }: { candidate: Candidate; isWinner: boolean }) {
  return (
    <span className="ml-auto flex shrink-0 items-center gap-1">
      {candidate.isElite ? (
        <span title="Elite">
          <Star aria-hidden className="size-3.5 text-fg-muted" />
          <span className="sr-only">Elite</span>
        </span>
      ) : null}
      {isWinner ? (
        <span title="Winner">
          <Crown aria-hidden className="size-3.5 text-pass" />
          <span className="sr-only">Winner</span>
        </span>
      ) : null}
    </span>
  );
}

/** One version's visual, reused by the canvas node, lists and the hero header. Presentational only. */
export function CandidateCard({
  candidate,
  baseline,
  priority,
  variant,
  isWinner,
  winnerBy = "ai",
  selected,
  diffUnseen,
  actions,
  parentLabel,
}: CandidateCardProps) {
  const meta = statusMeta(candidate.status);
  const tests = lastTests(candidate);
  const base = candidate.isBaseline ? null : baseline;
  const rejected = candidate.status === "rejected";

  const frame = cn(
    "relative box-border rounded-lg border bg-surface transition-colors duration-200",
    candidate.status === "drafting" && "border-dashed border-border-strong",
    candidate.status === "fail" || candidate.status === "fixing" ? "border-fail" : "border-border",
    isWinner && "animate-winner-glow border-2 border-pass",
    rejected && "opacity-60",
    selected && "outline-2 outline-offset-2 outline-solid outline-foreground",
  );

  const dot = diffUnseen ? (
    <span className="absolute top-1.5 right-1.5 size-1.5 bg-pass">
      <span className="sr-only">Unseen changes</span>
    </span>
  ) : null;

  if (variant === "node") {
    const loading = candidate.status === "drafting";
    return (
      <div className={cn(frame, "flex h-[104px] w-[192px] flex-col justify-between overflow-hidden p-2.5")}>
        {dot}
        <div className="flex h-5 items-center gap-1.5">
          <StatusSquare status={meta.visual} />
          <span className="text-sm font-bold text-foreground">{candidate.label}</span>
          <OperatorBadge by={candidate.createdBy} size="sm" />
          <RoleIcons candidate={candidate} isWinner={isWinner} />
        </div>
        {loading ? (
          <div className="flex flex-col gap-1.5" aria-hidden>
            <Skeleton className="h-2 w-full rounded-[2px]" />
            <Skeleton className="h-2 w-2/3 rounded-[2px]" />
          </div>
        ) : (
          <p className="truncate text-xs text-fg-muted">{candidate.reason}</p>
        )}
        <div className="flex h-5 items-center">
          <NodeStatusRow candidate={candidate} baseline={baseline} priority={priority} />
        </div>
      </div>
    );
  }

  const tags = (
    <>
      {candidate.isBaseline ? <CandidateTag tag="baseline" /> : null}
      {candidate.isElite ? <CandidateTag tag="elite" /> : null}
      {isWinner ? <CandidateTag tag="winner" by={winnerBy} /> : null}
      {candidate.fixCount > 0 ? <CandidateTag tag="fixed" count={candidate.fixCount} /> : null}
      {rejected ? <CandidateTag tag="rejected" /> : null}
    </>
  );

  if (variant === "row") {
    return (
      <div className={cn(frame, "flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5")}>
        {dot}
        <span className="text-sm font-bold text-foreground">{candidate.label}</span>
        <StatusBadge status={candidate.status} size="sm" tests={tests && candidate.status !== "measured" ? tests : undefined} />
        <OperatorBadge by={candidate.createdBy} size="sm" />
        <span className="flex flex-wrap items-center gap-1.5">{tags}</span>
        <span className="min-w-[10rem] flex-1 truncate text-xs text-fg-muted">{candidate.reason}</span>
        {candidate.status === "measured" && !candidate.isBaseline ? (
          <MetricDelta
            metric={PRIORITY_METRIC[priority]}
            value={priorityValue(candidate.ppa, priority)}
            baseline={priorityBaseline(baseline, priority)}
            size="sm"
            showLabel
          />
        ) : null}
        {candidate.fitness ? (
          <span className="font-mono text-xs text-fg-muted">F {formatScore(candidate.fitness.score)}</span>
        ) : null}
        {actions ? <span className="ml-auto flex items-center gap-2">{actions}</span> : null}
      </div>
    );
  }

  // hero
  const parent = parentName(candidate, parentLabel);
  const ppa = candidate.ppa;
  return (
    <div className={cn(frame, "flex flex-col gap-3 p-4")}>
      {dot}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-xl font-bold text-foreground">{candidate.label}</span>
        <StatusBadge status={candidate.status} tests={tests && candidate.status !== "measured" ? tests : undefined} />
        <OperatorBadge by={candidate.createdBy} />
        {actions ? <span className="ml-auto flex items-center gap-2">{actions}</span> : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 empty:hidden">{tags}</div>
      <div className="flex flex-col gap-1">
        <p className="text-sm text-foreground">{candidate.reason}</p>
        <p className="text-xs text-fg-subtle">
          {parent ? `From ${parent}. ` : "First version. "}
          {candidate.round === 0 ? "Initial build." : `Round ${candidate.round}.`}
        </p>
      </div>
      {ppa && candidate.status === "measured" ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <MetricDelta metric="area" value={ppa.areaUm2} baseline={base?.areaUm2 ?? null} size="sm" showLabel />
          <MetricDelta metric="clockPeriod" value={ppa.clockPeriodNs} baseline={base?.clockPeriodNs ?? null} size="sm" showLabel />
          <MetricDelta metric="power" value={ppa.powerMw} baseline={base?.powerMw ?? null} size="sm" showLabel />
          {candidate.fitness ? (
            <span className="font-mono text-xs text-fg-muted">F {formatScore(candidate.fitness.score)}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
