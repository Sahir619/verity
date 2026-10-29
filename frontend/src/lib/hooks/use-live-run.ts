"use client";

import { useEffect } from "react";
import { isLiveConfigured } from "@/lib/env";
import { openEventStream } from "@/lib/sources/live";
import { useVerityStore } from "@/lib/store";

/** Three failed reconnects inside this window turn the connection into "error". */
const RECONNECT_WINDOW_MS = 30_000;
const MAX_FAILED_RECONNECTS = 3;
const CLOCK_STEP_MS = 250;
/**
 * After the run ends the stream stays open for events that follow the end (the sign-off checks run after the result shows,
 * about 20 to 90 s). The server ends the stream 5 s after the last event, so the browser reconnects until the server answers
 * 204 (nothing left); this is only the upper bound.
 */
const TRAILING_EVENTS_MS = 180_000;
const TERMINAL = new Set(["done", "error", "cap_reached", "stopped"]);

/**
 * Owns the EventSource for a live run. Events are validated by lib/sources/live.ts, deduped by seq,
 * and applied through the one reducer. Also keeps the playback clock moving between events
 * (countdown and elapsed time).
 */
export function useLiveRun(runId: string | null): void {
  useEffect(() => {
    if (!runId) return;
    const store = useVerityStore.getState();
    const known = store.source === "live" && store.runId === runId && store.snapshot.run.runId === runId;
    if (!known) store.adoptLiveRun(runId);
    if (!isLiveConfigured) {
      useVerityStore.getState().setConnection("error", "Live runs are offline right now.");
      return;
    }

    let failures: number[] = [];
    let clockBaseT = useVerityStore.getState().snapshot.run.tMs;
    let clockBaseAt = performance.now();
    let finished = false;
    let tail: number | undefined;
    const endTail = () => {
      window.clearTimeout(tail);
      close();
      useVerityStore.getState().setTailOpen(false);
    };

    const close = openEventStream(runId, {
      onOpen() {
        failures = [];
        if (finished) return; // a reconnect for the trailing events: the run itself has ended
        useVerityStore.getState().setConnection("open", null);
      },
      onEvent(e) {
        const st = useVerityStore.getState();
        if (e.seq <= st.snapshot.run.lastSeq) return; // duplicate after a reconnect
        clockBaseT = e.tMs;
        clockBaseAt = performance.now();
        st.ingestLive(e);
        if (e.t === "run" && TERMINAL.has(e.phase) && !finished) {
          finished = true;
          // Events after the end (a closing caption, the sign-off sheet) may still come; the server ends the stream after them.
          tail = window.setTimeout(endTail, TRAILING_EVENTS_MS);
          const after = useVerityStore.getState();
          after.setTailOpen(true);
          after.setConnection("closed");
          after.setStatus("ended");
          after.setClock(e.tMs);
        }
      },
      onError(closed) {
        // After the end the server may close the stream while the sign-off is still running (it waits only a few seconds
        // after the last event). EventSource then reconnects with Last-Event-ID and receives the rest; the server answers
        // 204 when nothing is left, and only that (or the TRAILING_EVENTS_MS bound) ends the tail.
        if (finished) {
          if (closed) endTail();
          return;
        }
        const now = Date.now();
        failures = failures.filter((t) => now - t < RECONNECT_WINDOW_MS).concat(now);
        if (closed || failures.length >= MAX_FAILED_RECONNECTS) {
          close();
          useVerityStore.getState().setConnection("error", "Lost connection to the live run.");
        } else {
          useVerityStore.getState().setConnection("retrying");
        }
      },
    });

    const clock = window.setInterval(() => {
      const st = useVerityStore.getState();
      if (st.source !== "live" || finished) return;
      if (st.snapshot.run.phase === "running" || st.snapshot.run.phase === "starting") {
        st.setClock(clockBaseT + (performance.now() - clockBaseAt));
      }
    }, CLOCK_STEP_MS);

    return () => {
      window.clearInterval(clock);
      window.clearTimeout(tail);
      close();
      useVerityStore.getState().setTailOpen(false);
      const st = useVerityStore.getState();
      if (!finished && st.source === "live" && st.runId === runId) st.setConnection("closed");
    };
  }, [runId]);
}
