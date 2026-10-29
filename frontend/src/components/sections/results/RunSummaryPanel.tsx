"use client";

import { CreditsMeter } from "@/components/domain/CreditsMeter";
import { HumanInputsCounter } from "@/components/domain/HumanInputsCounter";
import { RunLedger } from "@/components/domain/RunLedger";
import { PoweredBy } from "@/components/brand/PoweredBy";
import type { RunSummary } from "@/lib/contracts/view-types";
import { formatTests } from "@/lib/format";

export type RunSummaryPanelProps = {
  summary: RunSummary;
  winnerTests: { passed: number; total: number } | null;
};

export function RunSummaryPanel({ summary, winnerTests }: RunSummaryPanelProps) {
  const rows = [
    {
      label: "Winner tests",
      value: winnerTests ? `${winnerTests.passed} of ${winnerTests.total} pass` : "No winner",
      hint: winnerTests ? `Locked tests, ${formatTests(winnerTests.passed, winnerTests.total)}.` : undefined,
    },
    {
      label: "Versions tried",
      value: summary.rejected > 0 ? `${summary.versionsTried} (${summary.rejected} rejected)` : `${summary.versionsTried}`,
    },
    { label: "Rounds", value: `${summary.rounds}` },
    { label: "Fixes applied", value: `${summary.fixes}` },
    { label: "Human inputs", value: <HumanInputsCounter items={summary.humanInputItems} /> },
    {
      label: "Cost",
      value: (
        <CreditsMeter
          mode={summary.mode}
          usedUsd={summary.credits.usedUsd}
          recordedUsd={summary.recordedUsd ?? undefined}
        />
      ),
      hint: summary.mode === "replay" ? "Replays cost nothing. This shows what the original recorded run spent." : undefined,
    },
    { label: "Model", value: <PoweredBy layout="inline" /> },
    { label: "Tools", value: "iverilog, cocotb, Yosys and OpenSTA (post-synthesis estimate, no wires), EQY" },
  ];
  return (
    <section aria-labelledby="summary-heading" className="space-y-3 rounded-lg border bg-surface p-5">
      <h2 id="summary-heading" className="text-xl font-bold text-foreground">
        Run summary
      </h2>
      <RunLedger rows={rows} />
    </section>
  );
}
