"use client";

import { Fragment, useState } from "react";
import { Button } from "@/components/ui/button";
import { CandidateCard } from "@/components/domain/CandidateCard";
import { CandidateTag } from "@/components/domain/CandidateTag";
import { MetricDelta } from "@/components/domain/MetricDelta";
import { OperatorBadge } from "@/components/domain/OperatorBadge";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { formatFitness } from "@/components/sections/shared/fitness-text";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Candidate, Priority } from "@/lib/contracts/types";
import { formatTests } from "@/lib/format";

export type RankingTableProps = {
  /** Already ordered: measured versions by fitness, then the rest. */
  rows: Candidate[];
  candidates: Record<string, Candidate>;
  baseline: Candidate | null;
  winnerId: string | null;
  winnerBy: "ai" | "user" | null;
  priority: Priority;
  onMakeWinner(id: string): void;
};

function ConfirmText({ c, winner }: { c: Candidate; winner: Candidate | null }) {
  return (
    <>
      Make {c.label} the winner? Its fitness is {c.fitness ? formatFitness(c.fitness.score) : "not scored"};{" "}
      {winner ? `${winner.label} has ${winner.fitness ? formatFitness(winner.fitness.score) : "no score"}` : "there is no winner yet"}.
    </>
  );
}

export function RankingTable({ rows, candidates, baseline, winnerId, winnerBy, priority, onMakeWinner }: RankingTableProps) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const winner = winnerId ? (candidates[winnerId] ?? null) : null;
  const base = baseline?.ppa ?? null;
  const canOverride = (c: Candidate) => c.status === "measured" && c.id !== winnerId;
  const confirm = (id: string) => {
    onMakeWinner(id);
    setConfirmId(null);
  };

  const rankOf = new Map<string, number>();
  rows.filter((c) => c.ppa !== null || c.isBaseline).forEach((c, i) => rankOf.set(c.id, i + 1));
  return (
    <section aria-labelledby="ranking-heading" className="space-y-3">
      <h2 id="ranking-heading" className="text-xl font-bold text-foreground">
        All versions
      </h2>

      <div className="hidden rounded-lg border bg-surface md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">#</TableHead>
              <TableHead>Version</TableHead>
              <TableHead>Made by</TableHead>
              <TableHead>From</TableHead>
              <TableHead>Tests</TableHead>
              <TableHead>Area</TableHead>
              <TableHead>Period</TableHead>
              <TableHead>Power</TableHead>
              <TableHead>Fitness</TableHead>
              <TableHead className="w-28">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((c) => {
              const rank = rankOf.get(c.id);
              const v = c.verification;
              return (
                <Fragment key={c.id}>
                  <TableRow className="group">
                    <TableCell className="font-mono text-fg-muted">{rank ?? "-"}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-bold text-foreground">{c.label}</span>
                        {c.id === winnerId ? <CandidateTag tag="winner" by={winnerBy ?? "ai"} /> : null}
                        {c.isElite && c.id !== winnerId ? <CandidateTag tag="elite" /> : null}
                        {c.isBaseline ? <CandidateTag tag="baseline" /> : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <OperatorBadge by={c.createdBy} size="sm" />
                    </TableCell>
                    <TableCell className="text-fg-muted">
                      {c.parentId ? (candidates[c.parentId]?.label ?? c.parentId) : "none"}
                    </TableCell>
                    <TableCell className="font-mono">{v ? formatTests(v.passed, v.total) : "n/a"}</TableCell>
                    {c.isBaseline ? (
                      <TableCell colSpan={3} className="text-fg-subtle">
                        baseline
                      </TableCell>
                    ) : c.ppa ? (
                      <>
                        <TableCell>
                          <MetricDelta metric="area" value={c.ppa.areaUm2} baseline={base?.areaUm2 ?? null} size="sm" />
                        </TableCell>
                        <TableCell>
                          <MetricDelta
                            metric="clockPeriod"
                            value={c.ppa.clockPeriodNs}
                            baseline={base?.clockPeriodNs ?? null}
                            size="sm"
                          />
                        </TableCell>
                        <TableCell>
                          <MetricDelta metric="power" value={c.ppa.powerMw} baseline={base?.powerMw ?? null} size="sm" />
                        </TableCell>
                      </>
                    ) : (
                      <TableCell colSpan={3}>
                        <StatusBadge status={c.status} size="sm" />
                      </TableCell>
                    )}
                    <TableCell className="font-mono">{c.fitness ? formatFitness(c.fitness.score) : "-"}</TableCell>
                    <TableCell>
                      {canOverride(c) ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
                          onClick={() => setConfirmId(c.id)}
                        >
                          Make winner
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                  {confirmId === c.id ? (
                    <TableRow>
                      <TableCell colSpan={10} className="bg-surface-2">
                        <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Confirm winner change">
                          <span className="text-sm text-foreground">
                            <ConfirmText c={c} winner={winner} />
                          </span>
                          <Button variant="outline" className="h-8 px-3" onClick={() => confirm(c.id)}>
                            Confirm
                          </Button>
                          <Button variant="ghost" className="h-8 px-3" onClick={() => setConfirmId(null)}>
                            Cancel
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <ul className="space-y-3 md:hidden" aria-label="All versions">
        {rows.map((c) => (
          <li key={c.id}>
            <CandidateCard
              candidate={c}
              baseline={base}
              priority={priority}
              variant="row"
              parentLabel={c.parentId ? (candidates[c.parentId]?.label ?? null) : null}
              isWinner={c.id === winnerId}
              winnerBy={c.id === winnerId ? (winnerBy ?? undefined) : undefined}
              actions={
                canOverride(c) ? (
                  confirmId === c.id ? (
                    <div className="space-y-2" role="group" aria-label="Confirm winner change">
                      <p className="text-sm text-foreground">
                        <ConfirmText c={c} winner={winner} />
                      </p>
                      <div className="flex gap-2">
                        <Button variant="outline" className="h-8 px-3" onClick={() => confirm(c.id)}>
                          Confirm
                        </Button>
                        <Button variant="ghost" className="h-8 px-3" onClick={() => setConfirmId(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button variant="outline" className="h-8 px-3" onClick={() => setConfirmId(c.id)}>
                      Make winner
                    </Button>
                  )
                ) : undefined
              }
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
