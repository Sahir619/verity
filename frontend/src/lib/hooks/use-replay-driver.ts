"use client";

import { useEffect, useRef } from "react";
import { loadRunLog } from "@/lib/sources/replay";
import { useVerityStore } from "@/lib/store";
import { useReducedMotion } from "./use-reduced-motion";

/** The Landing replay holds its final frame this long before looping. */
const LOOP_HOLD_MS = 6000;
/** Store writes per second while playing are capped by batching frames into steps of at least this size. */
const TICK_STEP_MS = 33;
/** A hidden tab must not make the replay jump when it comes back. */
const MAX_FRAME_MS = 250;

/**
 * Loads and validates a recorded run, then drives the store clock with requestAnimationFrame.
 * If the store already holds this recording (Landing hero to Studio), playback continues instead of reloading.
 * Replay never calls the agent: it only reads the static recording.
 */
export function useReplayDriver(opts: { src: string; autoplay: boolean; loop: boolean }): void {
  const { src, autoplay, loop } = opts;
  const reduced = useReducedMotion();
  const reducedRef = useRef(reduced);
  const loopRef = useRef(loop);
  const reloadKey = useVerityStore((s) => s.reloadKey);

  useEffect(() => {
    reducedRef.current = reduced;
    loopRef.current = loop;
  }, [reduced, loop]);

  // Load (or continue).
  useEffect(() => {
    const store = useVerityStore.getState();
    let cancelled = false;

    const startIfWanted = () => {
      const st = useVerityStore.getState();
      if (!autoplay || st.status !== "ready") return;
      if (reducedRef.current) {
        // Reduced motion: start paused on the final frame.
        st.seek(st.durationMs);
        st.setStatus("paused");
      } else {
        st.play();
      }
    };

    const holdsThisRecording = store.source === "replay" && store.src === src && store.status !== "error" && store.status !== "loading";
    if (holdsThisRecording) {
      startIfWanted();
      return;
    }

    store.beginReplayLoad();
    loadRunLog(src).then((res) => {
      if (cancelled) return;
      const st = useVerityStore.getState();
      if (!res.ok) {
        st.failReplayLoad(res.error);
        return;
      }
      st.load(res.log, src);
      startIfWanted();
    });
    return () => {
      cancelled = true;
    };
  }, [src, autoplay, reloadKey]);

  // Clock.
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let acc = 0;
    let holdUntil = 0;
    const frame = (now: number) => {
      const dt = Math.min(now - last, MAX_FRAME_MS);
      last = now;
      const st = useVerityStore.getState();
      if (st.source === "replay") {
        if (st.status === "playing") {
          holdUntil = 0;
          acc += dt;
          if (acc >= TICK_STEP_MS) {
            st.tick(acc);
            acc = 0;
          }
        } else if (st.status === "ended" && loopRef.current) {
          acc = 0;
          if (holdUntil === 0) holdUntil = now + LOOP_HOLD_MS;
          else if (now >= holdUntil) {
            holdUntil = 0;
            st.restart();
          }
        } else {
          acc = 0;
          holdUntil = 0;
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);
}
