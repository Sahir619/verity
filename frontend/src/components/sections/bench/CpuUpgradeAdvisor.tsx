"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { CpuGoal, CpuProfile, UpgradesResponse } from "@/lib/contracts/cpu";
import { formatArea, formatCpuNumber, formatDelta, formatMicros, formatNs } from "@/lib/format";
import { getCpuUpgrades, postCpuUpgrade } from "@/lib/sources/live";

const TARGETS: Record<string, string> = {
  cpi: "Cycles per instruction", "cycles_by_stall.busy": "Busy cycles", "cycles_by_stall.fetch": "Fetch waits",
  "cycles_by_stall.data": "Data waits", "area_by_block_um2.rv32i_rf": "Register-file area", bench_time_us: "Benchmark runtime", critical_path_blocks: "Critical path",
};
const NEEDS: Record<string, string> = {
  rvfi: "RISC-V formal proof", new_isa_key: "New ISA contract", recompile: "Recompiled benchmarks",
  memory_protocol: "Updated memory interface", control_contract: "Updated control contract",
  register_file_equivalence: "Register-file equivalence proof", flash_profile: "A flash-like memory profile",
};

function ProfileCard({ profile, title }: { profile: CpuProfile; title: string }) {
  const stalls = Object.entries(profile.cyclesByStall);
  const total = stalls.reduce((sum, [, cycles]) => sum + cycles, 0);
  const instructions = profile.bench.filter((b) => b.passed).reduce((sum, b) => sum + b.instructions, 0);
  const areas = Object.entries(profile.areaByBlockUm2).sort((a, b) => b[1] - a[1]);
  const totalArea = areas.reduce((sum, [, area]) => sum + area, 0);
  const segmentClasses = ["bg-foreground", "bg-muted-foreground", "bg-border"];
  return <div className="rounded-lg border border-border bg-card p-4">
    <h4 className="font-semibold">{title}</h4>
    <p className="mt-2 font-mono tabular-nums">CPI {formatCpuNumber(profile.cpi)} <span className="text-muted-foreground">/ clock {profile.clockNs === null ? "Not measured" : formatNs(profile.clockNs)}</span></p>
    <div className="mt-3 flex h-3 overflow-hidden rounded-sm bg-muted" aria-hidden="true">
      {stalls.map(([name, cycles], i) => <span key={name} className={segmentClasses[i]} style={{ width: `${total > 0 ? cycles / total * 100 : 0}%` }} />)}
    </div>
    <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {stalls.map(([name, cycles]) => <div key={name}><dt className="inline capitalize">{name}: </dt><dd className="inline font-mono tabular-nums">{instructions > 0 ? `${formatCpuNumber(cycles / instructions)} cycles/instruction` : `${formatCpuNumber(cycles, 0)} cycles`}</dd></div>)}
    </dl>
    <p className="mt-3 text-sm">Benchmark time: <span className="font-mono tabular-nums">{formatMicros(profile.benchTimeUs)}</span></p>
    <h5 className="mt-4 text-sm font-semibold">Area by block</h5>
    {areas.length === 0 ? <p className="text-sm text-muted-foreground">Block areas were not measured.</p> : <ul className="mt-2 space-y-2">
      {areas.map(([block, area]) => <li key={block}>
        <div className="flex flex-wrap justify-between gap-2 text-sm"><span>{block}</span><span className="font-mono tabular-nums">{formatArea(area)}</span></div>
        <div className="mt-1 h-1 bg-muted" aria-hidden="true"><div className="h-full bg-muted-foreground" style={{ width: `${totalArea > 0 ? area / totalArea * 100 : 0}%` }} /></div>
      </li>)}
    </ul>}
    <p className="mt-4 text-sm">Slowest path: {profile.criticalPathBlocks?.length ? profile.criticalPathBlocks.join(" > ") : "Not reported by OpenSTA"}</p>
  </div>;
}

export function CpuUpgradeAdvisor({ id, revision, goal }: { id: string; revision: string; goal: CpuGoal }) {
  const [result, setResult] = useState<{ id: string; revision: string; advice?: UpgradesResponse; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const [submission, setSubmission] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    const ac = new AbortController();
    void getCpuUpgrades(id, ac.signal).then((response) => {
      if (!ac.signal.aborted) setResult(response.ok ? { id, revision, advice: response.advice } : { id, revision, error: response.error });
    });
    return () => ac.abort();
  }, [id, revision, retry]);
  const current = result?.id === id && result.revision === revision ? result : null;
  const advice = current?.advice;
  const recommended = advice?.options.find((option) => option.recommended && option.buildable);
  const buildable = advice?.options.filter((option) => option.buildable) ?? [];
  // The user's pick if it can still be built, else the recommended option.
  const target = buildable.find((option) => option.id === choice) ?? recommended;
  async function confirm() {
    if (!target || pending) return;
    setPending(true);
    const response = await postCpuUpgrade(id, target.id);
    if (response.ok) {
      setSubmission("CPU upgrade started. Opening its board.");
      router.push(`${pathname}?cpuJob=${encodeURIComponent(response.job.id)}`);
    } else setSubmission(response.error);
    setPending(false);
  }
  return <section className="mt-6 space-y-4 border-t border-border pt-6" aria-label="CPU upgrade advisor">
    <header><h3 className="text-xl font-semibold">Choose the next CPU</h3><p className="mt-1 text-sm text-muted-foreground">Ranked for {goal} against this CPU profile. Every prediction is calculated by code.</p></header>
    {!current ? <div role="status" aria-busy="true"><p className="text-sm">Checking the CPU profile and upgrade sources...</p><div className="mt-3 h-32 rounded-lg border border-border bg-muted" aria-hidden="true" /></div> : null}
    {current?.error ? <div role="alert"><p>{current.error}</p><button className="bx ghost mt-2 min-h-10 focus-visible:outline-2 focus-visible:outline-ring" type="button" onClick={() => { setResult(null); setRetry((n) => n + 1); }}>Try again</button></div> : null}
    {advice ? <>
      <p id={`advice-${id}`} className="text-sm text-muted-foreground">{advice.message}</p>
      {advice.profile ? <ProfileCard profile={advice.profile} title="Measured CPU profile, fast memory" /> : <p className="rounded-lg border border-border p-4 text-sm">Benchmarks have not supplied a profile yet. Upgrade estimates will appear when profiling is available.</p>}
      {advice.flashProfile ? <ProfileCard profile={advice.flashProfile} title="Measured CPU profile, flash-like memory" /> : null}
      {advice.options.length ? <ol className="space-y-3">{advice.options.map((option) => <li key={option.id} className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap justify-between gap-2"><h4 className="font-semibold">{option.label}</h4><span className="text-sm">{option.recommended ? "Recommended" : option.reason}</span></div>
        {option.buildable ? <label className="mt-2 flex min-h-10 cursor-pointer items-center gap-2 text-sm">
          <input type="radio" name={`upgrade-${id}`} value={option.id} checked={target?.id === option.id} disabled={pending} onChange={() => setChoice(option.id)} className="accent-foreground focus-visible:ring-2 focus-visible:ring-ring" />Build this one
        </label> : null}
        <p className="mt-1 text-sm text-muted-foreground">Targets: {option.targets.map((target) => TARGETS[target] ?? target).join(", ")}</p>
        <dl className="mt-3 grid grid-cols-1 gap-2 font-mono text-sm tabular-nums sm:grid-cols-3">
          <div><dt>CPI estimate{option.prediction.basisWaitCycles > 0 ? " vs flash-like memory" : ""}</dt><dd>{formatCpuNumber(option.prediction.cpi)}</dd></div>
          <div><dt>Area estimate vs this CPU</dt><dd>{formatDelta(1 + option.prediction.areaDeltaPct / 100, 1, { lowerIsBetter: true }).text}</dd></div>
          <div><dt>Clock period vs this CPU</dt><dd>{formatDelta(1 + option.prediction.clockDeltaPct / 100, 1, { lowerIsBetter: true }).text}</dd></div>
          <div><dt>Runtime estimate vs this CPU</dt><dd>{option.prediction.timeRatio === null ? "Not measured" : formatDelta(option.prediction.timeRatio, 1, { lowerIsBetter: true }).text}</dd></div>
          <div><dt>Power proxy vs this CPU</dt><dd>{formatDelta(option.prediction.powerRatio, 1, { lowerIsBetter: true }).text}</dd></div>
        </dl>
        <p className="mt-3 text-sm">Needs: {option.needs.map((need) => NEEDS[need] ?? need).join(", ")}</p>
        <details className="mt-2 text-sm"><summary className="min-h-10 cursor-pointer py-2 focus-visible:outline-2 focus-visible:outline-ring">Formula and assumptions</summary><p className="break-words font-mono">{option.prediction.formula}</p><p className="mt-2 text-muted-foreground">{option.prediction.assumptions}</p></details>
        <ul className="mt-2 flex flex-wrap gap-x-4">{option.sources.map((source) => <li key={source.url}><a className="inline-block min-h-10 py-2 text-sm underline focus-visible:outline-2 focus-visible:outline-ring" href={source.url} target="_blank" rel="noreferrer">{source.title}</a></li>)}</ul>
      </li>)}</ol> : null}
    </> : null}
    <button type="button" className="min-h-10 rounded-md border border-border bg-primary px-4 py-2 font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring" disabled={!target || pending} aria-describedby={advice ? `advice-${id}` : undefined} onClick={() => void confirm()}>{pending ? "Starting CPU upgrade..." : "Confirm: build CPU v2"}</button>
    {submission ? <p role="status" className="text-sm">{submission}</p> : null}
  </section>;
}
