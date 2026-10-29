"use client";

import { useEffect, useMemo, useState } from "react";
import type { CandidateStatus, RunPhase } from "@/lib/contracts/types";
import { formatArea, formatTests, formatUsd } from "@/lib/format";
import { useCpuJob } from "@/lib/hooks/use-cpu-job";
import { initialSnapshot, reduce } from "@/lib/run/reducer";
import { loadLiveRunLog } from "@/lib/sources/live";
import type { LoadResult } from "@/lib/sources/replay";
import { CpuStatus } from "./CpuBoard";
import { TERMINAL, humanRefOf } from "./derive";
import { ResultModule } from "./ResultModule";

const PHASE_WORD: Record<RunPhase, string> = {
  idle: "Starting", starting: "Starting", running: "Running", done: "Finished", error: "Ended with an error", cap_reached: "Stopped at the cost cap", stopped: "Stopped",
};
const VERSION_WORD: Record<CandidateStatus, string> = {
  drafting: "Writing", linting: "Checking", simulating: "Testing", fixing: "Fixing", synthesizing: "Measuring", fail: "Failing", pass: "Passed", measured: "Measured", rejected: "Rejected",
};

/** What the server said, in a sentence. loadLiveRunLog reports the HTTP status in brackets. */
function readError(error: string): string {
  if (/\((401|403)\)/.test(error)) return "This run is still going. Until it finishes, only the session that started it can read its log.";
  if (/\(404\)/.test(error)) return "The server has no log for this run.";
  if (/could not be read/i.test(error)) return "The run log came back in a shape this page does not understand.";
  return "The run log could not be loaded. Check that the agent is running, then try again.";
}

/** Shown when the log is not readable: the state of this run's row on the CPU board, which the server always reports. */
function BoardRowStatus({ jobId, runId }: { jobId: string; runId: string }) {
  const { job } = useCpuJob(jobId);
  if (!job) return null;
  const block = job.blocks.find((b) => b.runId === runId);
  const status = block ? block.status : job.core.runId === runId ? job.core.status : null;
  const name = block ? block.title : job.core.runId === runId ? "Integrated core" : null;
  return <p className="mt-3 text-sm">
    {name && status ? <><span className="text-muted-foreground">{name} on the CPU board: </span><CpuStatus status={status} /></> : <span className="text-muted-foreground">This run is an earlier attempt, so it has no row on the current board. The job is <CpuStatus status={job.status} />.</span>}
  </p>;
}

/**
 * One run, shown inside the CPU panel. Reads GET /runs/{id}/log and folds it with the same reducer the bench uses, into a
 * local snapshot: the page's own run in the global store is never touched. Every number comes from the run log.
 */
export function CpuRunView({ jobId, runId, title }: { jobId: string; runId: string; title: string }) {
  const [attempt, setAttempt] = useState(0);
  const key = `${runId}:${attempt}`;
  const [loaded, setLoaded] = useState<{ key: string; res: LoadResult } | null>(null);

  useEffect(() => {
    let live = true;
    const res = loadLiveRunLog(runId);
    void res.then((r) => { if (live) setLoaded({ key, res: r }); });
    return () => { live = false; };
  }, [runId, key]);

  // A retry keeps the last answer on screen until the new one arrives, so the focused button is not removed under the reader.
  const result = loaded?.res ?? null;
  const busy = loaded?.key !== key;
  const view = useMemo(() => {
    if (!result?.ok) return null;
    const { log } = result;
    // Canvas ops only move nodes around, which this view does not draw, so they are skipped.
    let snap = initialSnapshot({ runId, mode: "replay", sample: log.meta.sample, title: log.meta.title || title });
    for (const e of log.events) if (e.t !== "op") snap = reduce(snap, e);
    return { run: snap.run, events: log.events, human: humanRefOf(log.events), sample: log.meta.sample };
  }, [result, runId, title]);

  const retry = (label: string) => <button type="button" className="bx ghost mt-2" aria-busy={busy} onClick={() => setAttempt((n) => n + 1)}>{busy ? "Loading..." : label}</button>;

  if (!result) return <p role="status" aria-busy>Loading the run...</p>;
  if (!view) {
    return <div>
      <div role="alert" className="rounded-lg border border-border p-3"><p>{result.ok ? "" : readError(result.error)}</p>{retry("Try again")}</div>
      <BoardRowStatus jobId={jobId} runId={runId} />
    </div>;
  }

  const { run } = view;
  const ended = TERMINAL.has(run.phase);
  return <div className="space-y-5 text-foreground">
    <div>
      <p className="inline-flex items-center gap-2 text-sm"><span aria-hidden>{run.phase === "done" ? "■" : ended ? "□" : "▣"}</span>{PHASE_WORD[run.phase]}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {view.sample ? "Sample data. " : ""}
        <span className="font-mono tabular-nums">{run.candidateOrder.length}</span> version{run.candidateOrder.length === 1 ? "" : "s"}, <span className="font-mono tabular-nums">{formatUsd(run.credits.usedUsd)}</span> spent.
      </p>
      <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{runId}</p>
    </div>
    {!ended ? <div role="status" className="rounded-lg border border-border p-3 text-sm">
      <p>This run is still going. What follows is the log so far.</p>{retry("Refresh")}
    </div> : null}
    {ended
      ? <ResultModule run={run} hasRun human={view.human} humanFeature priority={run.spec?.priority ?? "area"} events={view.events} />
      : null}
    {run.candidateOrder.length > 0 ? <section className="b-sec" aria-label="Versions in this run">
      <h3 className="b-sec-h">Versions in this run</h3>
      <table className="b-score">
        <thead><tr><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Tests</th><th scope="col">Area</th></tr></thead>
        <tbody>{run.candidateOrder.map((id) => {
          const c = run.candidates[id];
          if (!c) return null;
          const word = id === run.winnerId ? "Winner" : VERSION_WORD[c.status];
          const good = id === run.winnerId || c.status === "pass" || c.status === "measured";
          return <tr key={id}>
            <th scope="row">{c.label}</th>
            <td><span aria-hidden>{good ? "■" : "□"}</span> {word}</td>
            <td className="m">{c.verification ? formatTests(c.verification.passed, c.verification.total) : "Not run"}</td>
            <td className="m">{c.ppa?.areaUm2 != null ? formatArea(c.ppa.areaUm2) : "Not measured"}</td>
          </tr>;
        })}</tbody>
      </table>
    </section> : null}
  </div>;
}
