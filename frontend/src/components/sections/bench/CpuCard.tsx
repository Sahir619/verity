"use client";

import { useId, useRef, useState } from "react";
import { CPU_BLOCKS, UPGRADE_KIND_IDS, type CpuGoal, type CpuJobView, type CpuKind } from "@/lib/contracts/cpu";
import { formatUsd } from "@/lib/format";
import { useCpuJob } from "@/lib/hooks/use-cpu-job";
import { postCpuJob, dispatchProfileChange } from "@/lib/sources/live";
import { VerdictLine } from "./Research";

export const CPU_GOALS: { id: CpuGoal; label: string }[] = [
  { id: "area", label: "Smallest area" }, { id: "speed", label: "Fastest clock" }, { id: "power", label: "Lowest power" },
];

const JOB_WORD: Record<CpuJobView["status"], string> = { running: "Running", integrating: "Integrating", improving: "Improving", passed: "Passed", failed: "Failed", stopped: "Stopped" };

/** The chat's short note for a started job: its live status, goal and spend. The board itself lives in the right panel. */
function CpuJobLine({ jobId, initial, onShowBoard }: { jobId: string; initial?: CpuJobView; onShowBoard(jobId: string): void }) {
  const { job, error } = useCpuJob(jobId, initial);
  const mark = job?.status === "passed" ? "■" : job && (job.status === "running" || job.status === "integrating" || job.status === "improving") ? "▣" : "□";
  return (
    <section className="b-ai" aria-label="CPU job">
      <div className="rounded-lg border border-border bg-card p-4 text-card-foreground">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">CPU job started</h2>
          <span className={`inline-flex items-center gap-2 text-sm ${job?.status === "passed" ? "text-pass" : "text-foreground"}`} role="status">
            {job ? <><span aria-hidden>{mark}</span>{JOB_WORD[job.status]}</> : error ? "Status could not be read" : "Loading status"}
          </span>
        </div>
        {job ? <p className="mt-1 text-sm text-muted-foreground">Goal: {job.goal}. <span className="font-mono tabular-nums">{formatUsd(job.spentUsd)}</span> spent.</p> : null}
        <button type="button" className="bx ghost mt-3" aria-controls="bench-panel" onClick={() => onShowBoard(jobId)}>Show board</button>
      </div>
    </section>
  );
}

/** Selection only prepares a plan. The sole mutation is the explicit Confirm submit. */
export function CpuCard({ kinds, live, canBuild, threadId, jobId, onStarted, onShowBoard }: {
  kinds: CpuKind[]; live: boolean; canBuild: boolean; threadId: string | null; jobId?: string; onStarted(id: string): void; onShowBoard(jobId: string): void;
}) {
  const id = useId();
  const [selected, setSelected] = useState<CpuKind | null>(null);
  const [goal, setGoal] = useState<CpuGoal>("area");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initial, setInitial] = useState<CpuJobView | undefined>();
  const inFlight = useRef(false);
  if (jobId) return <CpuJobLine key={jobId} jobId={jobId} initial={initial} onShowBoard={onShowBoard} />;
  return (
    <section className="b-ai" aria-label="CPU choice">
      <VerdictLine verdict="cpu_choice" />
      <div className="rounded-lg border border-border bg-card p-4 text-card-foreground">
        <h2 className="text-xl font-semibold">{selected ? "Review the CPU plan" : "Which CPU do you want to build?"}</h2>
        <p className="text-sm text-muted-foreground">Building starts only after Confirm.</p>
        {selected ? <button type="button" className="bx ghost my-3" disabled={!live || starting} onClick={() => setSelected(null)}>Change CPU kind</button> : <ul className="my-4 grid list-none gap-2 p-0">
          {kinds.map((kind) => {
            const available = kind.buildable && kind.id === "rv32i_micro";
            return <li key={kind.id} className="rounded-md border border-border p-3">
              <p className="font-semibold">{kind.name}</p>
              <p className="text-sm text-muted-foreground">{kind.summary}</p>
              {available ? <button type="button" className="bx ghost mt-2" disabled={!live || starting} onClick={() => { setSelected(kind); setError(null); }}>
                Choose this CPU
              </button> : <p className="text-sm text-muted-foreground"><span aria-hidden>□</span> {UPGRADE_KIND_IDS.has(kind.id) ? "Upgrade of a passed CPU v1, from its board" : "Not buildable yet"}</p>}
            </li>;
          })}
        </ul>}
        {kinds.length === 0 ? <p>No CPU kinds were supplied. Send your request again.</p> : null}
        {selected ? <form aria-label="CPU build plan" aria-busy={starting} onSubmit={async (e) => {
          e.preventDefault();
          if (!live || !canBuild || inFlight.current) return;
          inFlight.current = true;
          setStarting(true);
          setError(null);
          try {
            const result = await postCpuJob(goal, threadId ?? undefined);
            if (result.ok) {
              setInitial(result.job);
              onStarted(result.job.id);
              dispatchProfileChange();
            } else setError(result.error);
          } finally {
            inFlight.current = false;
            setStarting(false);
          }
        }}>
          <h3 className="text-lg font-semibold">CPU plan</h3>
          <p className="text-sm text-muted-foreground">{selected.name}. Each block runs against locked tests, then the winners are wired into the core.</p>
          <ol className="my-3 grid list-inside list-decimal gap-1 text-sm">
            {CPU_BLOCKS.map((b) => <li key={b.packId}>{b.title} <span className="font-mono text-muted-foreground">({b.packId})</span></li>)}
          </ol>
          <fieldset disabled={!live || starting} className="my-4">
            <legend className="mb-2 text-sm font-semibold">Goal</legend>
            <div className="flex flex-wrap gap-3">{CPU_GOALS.map((g) => <label key={g.id} className="flex min-h-10 cursor-pointer items-center gap-2 rounded-md border border-border px-3 text-sm">
              <input type="radio" name={`${id}-goal`} value={g.id} checked={goal === g.id} onChange={() => setGoal(g.id)} className="accent-foreground focus-visible:ring-2 focus-visible:ring-ring" />{g.label}
            </label>)}</div>
          </fieldset>
          {!canBuild ? <p className="b-note text-muted-foreground">Sign in to build a CPU.</p> : null}
          {error ? <p role="alert" className="my-2 text-sm">{error}</p> : null}
          <button type="submit" className="bx pri" disabled={!live || !canBuild || starting}>{starting ? "Starting CPU job..." : `Confirm: build ${CPU_BLOCKS.length} blocks`}</button>
        </form> : null}
      </div>
    </section>
  );
}
