import { RunLogSchema } from "@/lib/contracts/schemas";
import type { RunLog } from "@/lib/contracts/types";
import { devCheckFitness } from "@/lib/run/fitness";

export type LoadResult = { ok: true; log: RunLog } | { ok: false; error: string };

const inflight = new Map<string, Promise<LoadResult>>();

/**
 * Fetches and validates a recorded run (a static file). Every event passes the zod schema before the reducer sees it.
 * Concurrent loads of the same file share one request (React strict mode mounts effects twice in dev).
 * This module never touches the agent: replay is a static file, free and offline after load.
 */
export function loadRunLog(src: string): Promise<LoadResult> {
  const existing = inflight.get(src);
  if (existing) return existing;
  const p = fetchRunLog(src).finally(() => inflight.delete(src));
  inflight.set(src, p);
  return p;
}

async function fetchRunLog(src: string): Promise<LoadResult> {
  try {
    const res = await fetch(src);
    if (!res.ok) return { ok: false, error: `Could not load ${src} (${res.status})` };
    const parsed = RunLogSchema.safeParse(await res.json());
    if (!parsed.success) {
      if (process.env.NODE_ENV !== "production") console.warn("[verity] run log rejected by schema", parsed.error.issues);
      return { ok: false, error: "The recorded run could not be read." };
    }
    const log = parsed.data as RunLog;
    if (process.env.NODE_ENV !== "production") devCheckFitness(log);
    return { ok: true, log };
  } catch {
    return { ok: false, error: `Could not load ${src}` };
  }
}
