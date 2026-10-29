"use client";

import type { CpuJobView, ProfileComparison } from "@/lib/contracts/cpu";
import { formatArea, formatCpuNumber, formatDelta, formatMicros, formatNs } from "@/lib/format";

const NOT_MEASURED = "Not measured";
const num = (value: number | null, format: (n: number) => string): string => (value === null ? NOT_MEASURED : format(value));
/** A change the server computed from the two profiles. Lower is better for every measure here. */
const pct = (value: number | null): string => (value === null ? NOT_MEASURED : formatDelta(1 + value / 100, 1, { lowerIsBetter: true }).text);
const ratio = (value: number | null): string => (value === null ? NOT_MEASURED : formatDelta(value, 1, { lowerIsBetter: true }).text);
const us = (value: number | null): string => num(value, formatMicros);

function Row({ label, parent, child, change }: { label: string; parent: string; child: string; change: string }) {
  return <tr className="border-b border-border last:border-0">
    <th scope="row" className="p-3 font-normal">{label}</th>
    <td className="p-3 font-mono tabular-nums">{parent}</td>
    <td className="p-3 font-mono tabular-nums">{child}</td>
    <td className="p-3 font-mono tabular-nums">{change}</td>
  </tr>;
}

function Head({ cols }: { cols: string[] }) {
  return <thead className="border-y border-border bg-muted"><tr>{cols.map((c) => <th key={c} scope="col" className="p-3 font-semibold">{c}</th>)}</tr></thead>;
}

function memoryName(wait: number): string {
  return `${wait === 0 ? "Fast memory" : "Flash-like memory"}, ${wait} wait cycle${wait === 1 ? "" : "s"}`;
}

function ModeTable({ mode }: { mode: ProfileComparison }) {
  return <div className="overflow-x-auto rounded-lg border border-border">
    <table className="w-full text-left text-sm">
      <caption className="p-3 text-left text-muted-foreground">{memoryName(mode.waitCycles)}. Benchmarks on the integrated core, against the parent&apos;s CPU v1. Fewer cycles and a lower CPI are better.</caption>
      <Head cols={["Benchmark", "CPU v1 cycles", "This core cycles", "Cycles change", "CPU v1 CPI", "This core CPI"]} />
      <tbody>
        {mode.bench.map((b) => <tr key={b.name} className="border-b border-border">
          <th scope="row" className="p-3 font-normal">{b.name}</th>
          <td className="p-3 font-mono tabular-nums">{num(b.parentCycles, (n) => formatCpuNumber(n, 0))}</td>
          <td className="p-3 font-mono tabular-nums">{num(b.cycles, (n) => formatCpuNumber(n, 0))}</td>
          <td className="p-3 font-mono tabular-nums">{pct(b.cyclesDeltaPct)}</td>
          <td className="p-3 font-mono tabular-nums">{num(b.parentCpi, formatCpuNumber)}</td>
          <td className="p-3 font-mono tabular-nums">{num(b.cpi, formatCpuNumber)}</td>
        </tr>)}
        <tr>
          <th scope="row" className="p-3 font-normal">All benchmarks</th>
          <td className="p-3 font-mono tabular-nums" colSpan={3}>Benchmark time {us(mode.parent?.benchTimeUs ?? null)} to {us(mode.child?.benchTimeUs ?? null)} ({ratio(mode.benchTimeRatio)})</td>
          <td className="p-3 font-mono tabular-nums">{num(mode.parent?.cpi ?? null, formatCpuNumber)}</td>
          <td className="p-3 font-mono tabular-nums">{num(mode.child?.cpi ?? null, formatCpuNumber)} ({pct(mode.cpiDeltaPct)})</td>
        </tr>
      </tbody>
    </table>
  </div>;
}

/** An upgrade job next to its parent's CPU v1. Every number is computed by code on the server from two measured profiles. */
export function CpuComparison({ job }: { job: CpuJobView }) {
  const c = job.comparison;
  if (!c) return null;
  const note = job.versions[0]?.profileNote;
  return <section className="mt-5 space-y-4" aria-label="Against the parent CPU v1">
    <h3 className="text-lg font-semibold">Against the parent CPU v1</h3>
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-sm">
        <caption className="p-3 text-left text-muted-foreground">Area is the sum over the block winners. The clock period is the integrated core&apos;s measured clock period, which also sets the benchmark time. Lower is better.</caption>
        <Head cols={["Measure", "CPU v1", "This core", "Change"]} />
        <tbody>
          <Row label="Total area" parent={num(c.parentAreaUm2, formatArea)} child={num(c.areaUm2, formatArea)} change={pct(c.areaDeltaPct)} />
          <Row label="Core clock period" parent={num(c.parentClockNs, formatNs)} child={num(c.clockNs, formatNs)} change={pct(c.clockDeltaPct)} />
        </tbody>
      </table>
    </div>
    {c.fast ? <ModeTable mode={c.fast} /> : <p className="rounded-lg border border-border p-4 text-sm">{memoryName(0)}: no comparison, because one of the two cores was not measured.</p>}
    {c.flash ? <ModeTable mode={c.flash} /> : <p className="rounded-lg border border-border p-4 text-sm">{memoryName(3)}: no comparison, because one of the two cores was not measured.{note ? ` ${note}` : ""}</p>}
  </section>;
}
