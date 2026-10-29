"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useCpuJob } from "@/lib/hooks/use-cpu-job";
import { postCpuRetry } from "@/lib/sources/live";

const RETRY_WORD = { running: "Running", integrating: "Integrating", improving: "Improving", passed: "Passed", failed: "Failed", stopped: "Stopped" } as const;

/** A failed (or stopped) job whose core was already retried: the retry is the job to look at, so there is no second Retry button. */
export function CpuRetriedAs({ id }: { id: string }) {
  const { job } = useCpuJob(id);
  const mark = job?.status === "passed" ? "■" : job && job.status !== "failed" && job.status !== "stopped" ? "▣" : "□";
  return <section className="mb-4 rounded-lg border border-border bg-card p-4" aria-label="Core retry">
    <p className="font-semibold">Retried as CPU job <Link href={`/bench?cpuJob=${encodeURIComponent(id)}`} className="font-mono underline focus-visible:outline-2 focus-visible:outline-ring">{id}</Link></p>
    {job ? <p role="status" className="mt-1 text-sm"><span aria-hidden>{mark}</span> {RETRY_WORD[job.status]}</p> : null}
  </section>;
}

/** A failed job whose seven blocks all passed: only the integrated core is rebuilt. Only Confirm calls the API. */
export function CpuCoreRetry({ id }: { id: string }) {
  const [asking, setAsking] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  async function confirm() {
    if (pending) return;
    setPending(true);
    const response = await postCpuRetry(id);
    if (response.ok) {
      setMessage("Core retry started. Opening its board.");
      router.push(`${pathname}?cpuJob=${encodeURIComponent(response.job.id)}`);
    } else setMessage(response.error);
    setPending(false);
  }
  return <section className="mb-4 rounded-lg border border-border bg-card p-4" aria-label="Retry the core">
    <p className="font-semibold">All 7 blocks passed. Only the integrated core failed.</p>
    {asking ? <>
      <p className="mt-1 text-sm text-muted-foreground">Starts a new CPU job that reuses the 7 passed blocks and builds only the integrated core.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="min-h-10 rounded-md border border-border bg-primary px-4 py-2 font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring" disabled={pending} onClick={() => void confirm()}>{pending ? "Starting the core retry..." : "Confirm: retry the core"}</button>
        <button type="button" className="bx ghost min-h-10 focus-visible:outline-2 focus-visible:outline-ring" disabled={pending} onClick={() => { setAsking(false); setMessage(null); }}>Cancel</button>
      </div>
    </> : <button type="button" className="mt-3 min-h-10 rounded-md border border-border bg-primary px-4 py-2 font-semibold text-primary-foreground focus-visible:outline-2 focus-visible:outline-ring" onClick={() => setAsking(true)}>Retry the core</button>}
    {message ? <p role="status" className="mt-2 text-sm">{message}</p> : null}
  </section>;
}
