"use client";

import Link from "next/link";
import type { MouseEvent, ReactNode } from "react";
import { CPU_BLOCKS, cpuJobActive, cpuSpecKind, cpuSpecName, UPGRADE_LABELS, type CpuEvidence, type CpuJobView, type CpuTotals } from "@/lib/contracts/cpu";
import { formatUsd } from "@/lib/format";
import { useCpuJob } from "@/lib/hooks/use-cpu-job";
import { CpuComparison } from "./CpuComparison";
import { CpuCoreRetry, CpuRetriedAs } from "./CpuCoreRetry";
import { CpuUpgradeAdvisor } from "./CpuUpgradeAdvisor";

type Status = CpuJobView["status"] | CpuJobView["blocks"][number]["status"] | CpuJobView["core"]["status"];
const STATUS_WORD: Record<Status, string> = { running: "Running", integrating: "Integrating", improving: "Improving", passed: "Passed", failed: "Failed", stopped: "Stopped", waiting: "Waiting", locked: "Locked" };

/** Inside the panel a plain click opens a run in place (onOpenRun); a new-tab click, or anywhere else, is the bench page for that run. */
export type OpenRun = (runId: string, title: string) => void;

/** Always a real link, so a card keeps its heading and a new tab still works; inside the panel a plain click stays in the panel. */
function RunLink({ runId, title, onOpenRun, className, children }: { runId: string; title: string; onOpenRun?: OpenRun; className: string; children: ReactNode }) {
  const onClick = onOpenRun
    ? (e: MouseEvent<HTMLAnchorElement>) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      onOpenRun(runId, title);
    }
    : undefined;
  return <Link className={className} href={`/bench?run=${encodeURIComponent(runId)}`} onClick={onClick}>{children}</Link>;
}

export function CpuStatus({ status }: { status: Status }) {
  return <span className={`inline-flex items-center gap-2 text-sm ${status === "passed" ? "text-pass" : "text-foreground"}`}>
    <span aria-hidden>{status === "passed" ? "■" : status === "running" || status === "integrating" || status === "improving" ? "▣" : "□"}</span>{STATUS_WORD[status]}
  </span>;
}

const EQY_WORD: Record<NonNullable<CpuEvidence["eqyVsV1"]>, string> = {
  proven: "Proven equal to own v1", inconclusive: "Inconclusive", not_equivalent: "Not equivalent", not_run: "Not run", v1_is_winner: "Winner is its own v1",
};
const RESEARCH_WORD = { pending: "Research pending", ran: "Research read", reused: "Saved research read", skipped: "Ran without research (skipped)", failed: "Ran without research (failed)", off: "Ran without research (not configured)" } as const;

/** One short line per fact the server read from the sign-off sheet. A missing fact is left out, never filled in. */
export function evidenceParts(ev: CpuEvidence): string[] {
  const parts: string[] = [];
  if (ev.eqyVsV1) parts.push(`EQY: ${EQY_WORD[ev.eqyVsV1]}`);
  if (ev.testsPassed != null && ev.testsTotal != null) parts.push(`Tests ${ev.testsPassed}/${ev.testsTotal}`);
  if (ev.seedsPassed != null && ev.seedsRun != null) parts.push(`Seeds ${ev.seedsPassed}/${ev.seedsRun}`);
  if (ev.mutantsKilled != null && ev.mutantsTotal != null) parts.push(`Planted bugs caught ${ev.mutantsKilled}/${ev.mutantsTotal}`);
  if (ev.repairs != null) parts.push(`Repairs ${ev.repairs}`);
  if (ev.rvfiPassed != null && ev.rvfiTotal != null) parts.push(`riscv-formal ${ev.rvfiPassed}/${ev.rvfiTotal} checks passed`);
  if (ev.rvfiTimeouts != null && ev.rvfiTimeouts > 0) parts.push(`riscv-formal: ${ev.rvfiTimeouts} not proven within the time limit`);
  if (ev.rvfiFailed && ev.rvfiFailed.length > 0) parts.push(`riscv-formal failed: ${ev.rvfiFailed.join(", ")}`);
  if (ev.techniques && ev.techniques.length > 0) parts.push(`Techniques ${ev.techniques.join(", ")}`);
  return parts;
}

function Evidence({ ev }: { ev: CpuEvidence }) {
  const parts = evidenceParts(ev);
  return parts.length === 0 ? null : <p className="mt-1 text-xs text-muted-foreground">{parts.join(" · ")}</p>;
}

/** Totals are code-summed from block winners. A part the runs did not measure shows as not measured. */
function formatTotal(value: number | null, unit: string): string {
  return value == null || !Number.isFinite(value) ? "Not measured" : `${new Intl.NumberFormat("en", { maximumFractionDigits: 3 }).format(value)} ${unit}`;
}
function ratioVsV1(value: number | null, base: number | null): string {
  return value == null || base == null || base <= 0 ? "Not measured" : cpuDelta(value / base);
}

/** CPU v1 and each later version side by side. Every number is a total from run data, labelled against CPU v1. */
export function CpuVersions({ job, onOpenRun }: { job: CpuJobView; onOpenRun?: OpenRun }) {
  const versions = job.versions;
  if (versions.length === 0) return null;
  const base: CpuTotals = versions[0].totals;
  const rows: { label: string; unit: string; key: keyof CpuTotals }[] = [
    { label: "Total area", unit: "um2", key: "areaUm2" }, { label: "Longest block clock period", unit: "ns", key: "clockNs" }, { label: "Total estimated power", unit: "mW", key: "powerMw" },
  ];
  const blockTitle = (id: string | null) => job.blocks.find((b) => b.blockId === id)?.title ?? id ?? "";
  return <div className="overflow-x-auto rounded-lg border border-border">
    <table className="w-full text-left text-sm">
      <caption className="p-3 text-left text-muted-foreground">CPU versions. Each later version swaps in one better block winner (EQY proven equal to that block&apos;s own v1) and reruns the core. Changes are against CPU v1.</caption>
      <thead className="border-y border-border bg-muted"><tr>
        <th scope="col" className="p-3 font-semibold">Measure</th>
        {versions.map((v) => <th key={v.version} scope="col" className="p-3 font-semibold">{job.parentJobId ? "This core" : `CPU v${v.version}`}{v.changedBlock ? <><br /><span className="font-normal text-muted-foreground">New {blockTitle(v.changedBlock)}</span></> : null}</th>)}
      </tr></thead>
      <tbody>
        {rows.map((r) => <tr key={r.key} className="border-b border-border">
          <th scope="row" className="p-3 font-normal">{r.label}</th>
          {versions.map((v) => <td key={v.version} className="p-3 font-mono tabular-nums">{formatTotal(v.totals[r.key], r.unit)}{v.version > 1 ? <><br /><span className="text-muted-foreground">{ratioVsV1(v.totals[r.key], base[r.key])} vs CPU v1</span></> : null}</td>)}
        </tr>)}
        <tr>
          <th scope="row" className="p-3 font-normal">Core programs passed</th>
          {versions.map((v) => {
            const e = v.coreEvidence;
            return <td key={v.version} className="p-3">
              <span className="font-mono tabular-nums">{e.testsPassed != null && e.testsTotal != null ? `${e.testsPassed}/${e.testsTotal}` : "Not reported"}</span>
              {v.coreRunId ? <><br /><RunLink className="underline focus-visible:outline-2 focus-visible:outline-ring" runId={v.coreRunId} title={job.parentJobId ? "Core run" : `CPU v${v.version} core run`} onOpenRun={onOpenRun}>Core run</RunLink></> : null}
              {v.blockRunId ? <><br /><RunLink className="underline focus-visible:outline-2 focus-visible:outline-ring" runId={v.blockRunId} title={`New ${blockTitle(v.changedBlock)} run`} onOpenRun={onOpenRun}>New block run</RunLink></> : null}
              {v.blockEvidence ? <Evidence ev={v.blockEvidence} /> : null}
            </td>;
          })}
        </tr>
      </tbody>
    </table>
  </div>;
}

function RunCard({ title, packId, status, runId, onOpenRun, children }: {
  title: string; packId: string | null; status: Status; runId: string | null; onOpenRun?: OpenRun; children?: ReactNode;
}) {
  const content = <>
    <div className="flex flex-wrap items-start justify-between gap-2"><h3 className="font-semibold">{title}</h3><CpuStatus status={status} /></div>
    <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{packId ?? "Core pack awaits block winners"}</p>
    {children}
    <p className="mt-3 text-sm text-muted-foreground">{runId ? "Open run →" : status === "locked" ? "Waiting for all blocks to pass" : "No run started yet"}</p>
  </>;
  const classes = "block min-w-0 rounded-lg border border-border bg-card p-4 text-card-foreground";
  return runId
    ? <RunLink className={`${classes} hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2`} runId={runId} title={title} onOpenRun={onOpenRun}>{content}</RunLink>
    : <div className={classes}>{content}</div>;
}

/** Ratios are against this block's own first passing version. A missing measurement stays missing. */
export function cpuDelta(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio)) return "Not measured";
  const percent = (ratio - 1) * 100;
  const rounded = Math.round(percent * 10) / 10;
  if (rounded === 0) return percent === 0 ? "0%" : `${percent < 0 ? "-" : "+"}<0.1%`;
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(rounded)}%`;
}

/** Evidence is read by code on the server from each winner's sign-off sheet. A fact the run did not report shows as not reported. */
export function CpuResult({ job, onOpenRun }: { job: CpuJobView; onOpenRun?: OpenRun }) {
  const kind = cpuSpecKind(job.specId);
  return <section className="mt-5 space-y-4" aria-label="CPU result">
    <h2 className="text-xl font-semibold">CPU result</h2>
    <CpuVersions job={job} onOpenRun={onOpenRun} />
    <CpuComparison job={job} />
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="font-semibold">Configuration</h3>
      <p>{cpuSpecName(job.specId)}</p>
      <p className="text-sm text-muted-foreground">{kind?.summary ?? "Configuration is not in this bench's CPU catalog."}</p>
      {job.specId === "rv32i_micro" ? <p className="text-sm text-muted-foreground">RV32I base, 37 instructions. One clock, synchronous reset. No M or C extension, interrupts, privilege modes or misaligned access support.</p> : null}
      <p className="text-sm">Goal: {job.goal}. <span className="font-mono tabular-nums">{formatUsd(job.spentUsd)}</span> spent.</p>
    </div>
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-sm">
        <caption className="p-3 text-left text-muted-foreground">Each winner compared with that block&apos;s own v1. Lower area, clock period and estimated power are better.</caption>
        <thead className="border-y border-border bg-muted"><tr>{["Block / winner", "Area vs own v1", "Clock period vs own v1", "Power vs own v1", "EQY vs own v1"].map((x) => <th key={x} scope="col" className="p-3 font-semibold">{x}</th>)}</tr></thead>
        <tbody>{job.blocks.map((b) => <tr key={b.blockId} className="border-b border-border last:border-0">
          <th scope="row" className="p-3 font-normal">{b.title}<br /><span className="font-mono">{b.usedVersion ? `Uses ${b.usedVersion} (winner ${b.summary?.winner?.label ?? "not reported"} not proven)` : b.summary?.winner?.label ?? "Winner not reported"}</span></th>
          {(b.usedVersion ? [1, 1, 1] : [b.summary?.outcome?.areaVsV1, b.summary?.outcome?.clockVsV1, b.summary?.outcome?.powerVsV1]).map((v, i) => <td key={i} className="p-3 font-mono tabular-nums">{cpuDelta(v)}</td>)}
          <td className="p-3"><span aria-hidden>{b.evidence.eqyVsV1 === "proven" ? "■" : "□"}</span> {b.evidence.eqyVsV1 ? EQY_WORD[b.evidence.eqyVsV1] : "Not reported"}{b.runId ? <><br /><RunLink className="underline focus-visible:outline-2 focus-visible:outline-ring" runId={b.runId} title={`${b.title} run`} onOpenRun={onOpenRun}>View proof in run</RunLink></> : null}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="font-semibold">Core programs passed of total</h3>
      {job.core.evidence.testsPassed != null && job.core.evidence.testsTotal != null
        ? <p className="font-mono text-lg tabular-nums">{job.core.evidence.testsPassed} of {job.core.evidence.testsTotal}</p>
        : <p className="text-sm text-muted-foreground">The core run did not report its program counts.</p>}
      <Evidence ev={job.core.evidence} />
      {job.core.runId ? <RunLink className="bx ghost mt-2" runId={job.core.runId} title="Integrated core" onOpenRun={onOpenRun}>View core tests</RunLink> : null}
    </div>
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="font-semibold">How it was built</h3>
      <p className="text-sm text-muted-foreground">Block winners were frozen, then integrated into the core. The core uses a first version plus repair, with no optimization round. {job.versions.length > 1 ? "Later CPU versions swap in one better block winner and rerun the core." : ""}</p>
      <ul className="my-3 space-y-1 text-sm">{job.blocks.map((b) => <li key={b.blockId}>{b.title}: {b.reused ? `reused from ${job.retryOf ? "the failed CPU job" : "the parent CPU v1"}, no new run` : <><span className="font-mono tabular-nums">{b.attempts}</span> block run attempt{b.attempts === 1 ? "" : "s"}</>}{b.summary?.outcome ? <>, <span className="font-mono tabular-nums">{b.summary.outcome.versions}</span> versions in the reported run</> : null}{b.evidence.repairs != null ? <>, <span className="font-mono tabular-nums">{b.evidence.repairs}</span> repair{b.evidence.repairs === 1 ? "" : "s"}</> : null}. {RESEARCH_WORD[b.research]}.<Evidence ev={b.evidence} /></li>)}</ul>
      <p className="break-all font-mono text-xs text-muted-foreground">Core run: {job.core.runId ?? "Not reported"}</p>
    </div>
  </section>;
}

export function CpuBoard({ id, initial, onOpenRun }: { id: string; initial?: CpuJobView; onOpenRun?: OpenRun }) {
  const { job, error, retry } = useCpuJob(id, initial);
  return <section className="w-full min-w-0 text-foreground" aria-label="CPU board">
    {error ? <div role="alert" className="mb-4 rounded-lg border border-border p-3"><p>{error}</p>{job ? <p className="text-sm text-muted-foreground">Showing the last received board.</p> : null}<button type="button" className="bx ghost mt-2" onClick={retry}>Try again</button></div> : null}
    {!job ? <div role="status" aria-busy={!error}>
      <p>{error ? "The CPU board could not be loaded." : "Loading the CPU board..."}</p>
      {!error ? <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">{Array.from({ length: CPU_BLOCKS.length + 1 }, (_, i) => <div key={i} className="h-28 rounded-lg border border-border bg-muted" aria-hidden />)}</div> : null}
    </div> : <>
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="text-2xl font-semibold">CPU board</h2><p className="text-sm text-muted-foreground">{cpuSpecName(job.specId)}</p>
          {job.parentJobId ? <p className="text-sm">Upgrade of CPU v1{job.upgradeId ? `: ${UPGRADE_LABELS[job.upgradeId] ?? "an upgrade"}` : ""}. <Link href={`/bench?cpuJob=${encodeURIComponent(job.parentJobId)}`} className="underline focus-visible:outline-2 focus-visible:outline-ring">Open the parent CPU v1 board</Link></p> : null}
          {job.retryOf ? <p className="text-sm">Core retry of CPU job {job.retryOf}. <Link href={`/bench?cpuJob=${encodeURIComponent(job.retryOf)}`} className="underline focus-visible:outline-2 focus-visible:outline-ring">Open the failed CPU job board</Link></p> : null}</div>
        <CpuStatus status={job.status} />
      </header>
      <p className="mb-4 text-sm text-muted-foreground">Goal: {job.goal}. <span className="font-mono tabular-nums">{formatUsd(job.spentUsd)}</span> spent.</p>
      {job.message ? <p role="status" className="mb-4 text-sm">{job.message}</p> : null}
      {job.retryJobId ? <CpuRetriedAs key={job.retryJobId} id={job.retryJobId} /> : null}
      {job.status === "failed" && !job.retryJobId && !job.upgradeId && job.blocks.length === CPU_BLOCKS.length && job.blocks.every((b) => b.status === "passed") ? <CpuCoreRetry key={job.id} id={job.id} /> : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {job.blocks.map((b) => <RunCard key={b.blockId} title={b.title} packId={b.packId} status={b.status} runId={b.runId} onOpenRun={onOpenRun}>
          <p className="mt-2 font-mono text-xs tabular-nums text-muted-foreground">{b.reused ? `Reused from ${job.retryOf ? "the failed CPU job" : "the parent CPU v1"}, no new run` : <>{b.attempts} run attempt{b.attempts === 1 ? "" : "s"}</>}{b.usedVersion ? ` · Uses ${b.usedVersion}, winner not proven` : b.summary?.winner ? ` · Winner ${b.summary.winner.label}` : ""}</p>
          {b.research !== "pending" ? <p className="mt-1 text-xs text-muted-foreground">{RESEARCH_WORD[b.research]}</p> : null}
          <Evidence ev={b.evidence} />
        </RunCard>)}
        <RunCard title="Integrated core" packId={job.core.packId} status={job.core.status} runId={job.core.runId} onOpenRun={onOpenRun}>
          <Evidence ev={job.core.evidence} />
        </RunCard>
      </div>
      <p className="mt-3 text-sm text-muted-foreground">{cpuJobActive(job) ? "Updates every 3 seconds while building." : "This job has ended."} <Link href={`/bench?cpuJob=${encodeURIComponent(job.id)}`} className="underline focus-visible:outline-2 focus-visible:outline-ring">Link to this board</Link></p>
      {job.status === "passed" || job.status === "improving" ? <CpuResult job={job} onOpenRun={onOpenRun} /> : null}
      {job.status === "passed" && !job.parentJobId ? <CpuUpgradeAdvisor key={job.id} id={job.id} goal={job.goal} revision={`${job.versions.length}:${job.finishedAt ?? ""}`} /> : null}
    </>}
  </section>;
}
