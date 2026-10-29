"use client";

import { Crown } from "lucide-react";
import { CandidateCard } from "@/components/domain/CandidateCard";
import { EqyBadge } from "@/components/domain/EqyBadge";
import { HonestyNote } from "@/components/domain/HonestyNote";
import { MetricDelta } from "@/components/domain/MetricDelta";
import { PpaDeltaChart } from "@/components/domain/PpaDeltaChart";
import { SampleDataBadge } from "@/components/domain/SampleDataBadge";
import { formatFitness } from "@/components/sections/shared/fitness-text";
import type { Candidate, Priority } from "@/lib/contracts/types";

export type WinnerPanelProps = {
  winner: Candidate;
  baseline: Candidate | null;
  winnerBy: "ai" | "user";
  aiWinner: Candidate | null;
  partial: boolean;
  sample: boolean;
  priority: Priority;
  parentLabel: string | null;
};

const ROWS = [
  { label: "Area", metric: "area", pick: (c: Candidate) => c.ppa?.areaUm2 ?? null },
  { label: "Clock period", metric: "clockPeriod", pick: (c: Candidate) => c.ppa?.clockPeriodNs ?? null },
  { label: "Power", metric: "power", pick: (c: Candidate) => c.ppa?.powerMw ?? null },
] as const;

export function WinnerPanel({ winner, baseline, winnerBy, aiWinner, partial, sample, priority, parentLabel }: WinnerPanelProps) {
  const baselineLabel = baseline?.label ?? "v1";
  const isBaseline = winner.isBaseline;
  const picker =
    winnerBy === "user" ? `Picked by you (AI pick: ${aiWinner?.label ?? "none"})` : "Picked by fitness";

  return (
    <section aria-labelledby="winner-heading" className="space-y-5 rounded-lg border bg-surface p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="winner-heading" className="flex items-center gap-2 text-3xl font-bold text-foreground">
          <Crown aria-hidden className="size-6" />
          {partial ? `Best so far: ${winner.label}` : `Winner: ${winner.label}`}
        </h2>
        <p className="text-sm text-fg-muted">{picker}</p>
      </div>
      {winnerBy === "user" && aiWinner?.fitness ? (
        <p className="text-sm text-fg-muted">
          AI pick: {aiWinner.label} (fitness {formatFitness(aiWinner.fitness.score)})
        </p>
      ) : null}

      <CandidateCard
        candidate={winner}
        baseline={baseline?.ppa ?? null}
        priority={priority}
        variant="hero"
        isWinner
        winnerBy={winnerBy}
        parentLabel={parentLabel}
      />

      <EqyBadge eqy={winner.eqy} againstLabel={baselineLabel} />

      {winner.ppa && baseline?.ppa ? (
        <dl className="divide-y border-y">
          {ROWS.map((r) => (
            <div key={r.metric} className="grid grid-cols-[8rem_minmax(0,1fr)] items-center gap-3 py-2.5">
              <dt className="text-sm text-fg-muted">{r.label}</dt>
              <dd>
                <MetricDelta
                  metric={r.metric}
                  value={r.pick(winner)}
                  baseline={isBaseline ? r.pick(winner) : r.pick(baseline)}
                  showValue
                  size="md"
                />
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-sm text-fg-muted">Not measured. Versions that fail the locked tests are never synthesized.</p>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <HonestyNote variant="block" />
        {sample ? <SampleDataBadge /> : null}
      </div>

      {winner.ppa && baseline?.ppa && !isBaseline ? (
        <PpaDeltaChart
          baseline={baseline.ppa}
          candidate={winner.ppa}
          baselineLabel={baseline.label}
          candidateLabel={winner.label}
          sample={sample}
        />
      ) : null}
    </section>
  );
}
