"use client";

import { useEffect, useRef, useState } from "react";
import { cpuJobActive, type CpuJobView } from "@/lib/contracts/cpu";
import { getCpuJob } from "@/lib/sources/live";

export const CPU_POLL_MS = 3000;

/** One request at a time. Terminal jobs stop polling; errors keep the last board and offer a retry. */
export function useCpuJob(id: string, initial?: CpuJobView) {
  const [job, setJob] = useState<CpuJobView | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const lastJob = useRef(initial ?? null);
  useEffect(() => {
    const ac = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let latest = lastJob.current?.id === id ? lastJob.current : null;
    const read = async () => {
      const result = await getCpuJob(id, ac.signal);
      if (ac.signal.aborted) return;
      if (result.ok && result.job.id === id) {
        latest = result.job;
        lastJob.current = result.job;
        setJob(result.job);
        setError(null);
        if (cpuJobActive(result.job)) timer = setTimeout(read, CPU_POLL_MS);
      } else {
        setError(result.ok ? "The server returned a different CPU job. Try again." : result.error);
        if (latest && cpuJobActive(latest)) timer = setTimeout(read, CPU_POLL_MS);
      }
    };
    if (revision === 0 && initial?.id === id) {
      if (cpuJobActive(initial)) timer = setTimeout(read, CPU_POLL_MS);
    } else void read();
    return () => { ac.abort(); clearTimeout(timer); };
  }, [id, initial, revision]);
  return { job: job?.id === id ? job : null, error, retry: () => setRevision((n) => n + 1) };
}
