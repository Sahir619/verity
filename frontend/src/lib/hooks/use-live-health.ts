"use client";

import { useEffect, useState } from "react";
import { isLiveConfigured } from "@/lib/env";
import { checkHealth } from "@/lib/sources/live";
import type { LiveHealth } from "@/lib/contracts/view-types";

const RECHECK_EVENT = "verity:health-recheck";

/** Asks every mounted useLiveHealth to check GET /health again, e.g. after a Send or a Run failed. Cheap and free. */
export function recheckHealth(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(RECHECK_EVENT));
}

/**
 * GET /health on mount, and again whenever recheckHealth() is called. "not_configured" when NEXT_PUBLIC_AGENT_URL is
 * unset (no request is made). A recheck keeps the current value until the new answer lands, so nothing flashes. A recheck that
 * fails keeps the last known features (and whether a code is needed): a server that did not answer once has not lost what it
 * can do, and a chat thread must not change path because of it.
 */
export function useLiveHealth(): LiveHealth {
  const [health, setHealth] = useState<LiveHealth>(() => ({
    status: isLiveConfigured ? "checking" : "not_configured",
    capUsd: null,
  }));

  useEffect(() => {
    if (!isLiveConfigured) return;
    let ac = new AbortController();
    const load = () => {
      ac.abort(); // only the newest answer counts
      const mine = new AbortController();
      ac = mine;
      checkHealth(mine.signal).then((h) => {
        if (mine.signal.aborted) return;
        setHealth((prev) => (h.status === "error" && prev.features ? { ...h, features: prev.features, authRequired: prev.authRequired } : h));
      });
    };
    load();
    window.addEventListener(RECHECK_EVENT, load);
    return () => {
      window.removeEventListener(RECHECK_EVENT, load);
      ac.abort();
    };
  }, []);

  return health;
}
