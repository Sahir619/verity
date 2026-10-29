"use client";

import { useCallback, useEffect, useState } from "react";
import { isLiveConfigured } from "@/lib/env";
import { listRuns, type ServerRun } from "@/lib/sources/live";

/** What GET /runs gave the workspace page. "off" is a site built without an agent URL: no request is made. */
export type RunsList =
  | { status: "loading" }
  | { status: "off" }
  | { status: "error" }
  | { status: "ready"; runs: ServerRun[] };

/**
 * GET /runs once on mount, and again when `reload` is called. Keeps the last answer while a reload is in flight, so the
 * cards do not blink. Runs are looked up by id from this list; the server's page is the newest 100.
 */
export function useRunsList(): { list: RunsList; reload(): void } {
  const [list, setList] = useState<RunsList>(() => (isLiveConfigured ? { status: "loading" } : { status: "off" }));
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!isLiveConfigured) return;
    const ac = new AbortController();
    listRuns(ac.signal).then((res) => {
      if (ac.signal.aborted) return;
      setList(res.ok ? { status: "ready", runs: res.runs } : { status: "error" });
    });
    return () => ac.abort();
  }, [tick]);

  const reload = useCallback(() => {
    setList((l) => (l.status === "error" ? { status: "loading" } : l));
    setTick((n) => n + 1);
  }, []);
  return { list, reload };
}
