"use client";

import { useVerityStore } from "@/lib/store";

/**
 * Derives the plan gate countdown from run.gate and the playback clock (replay position or live elapsed time).
 * With Autopilot off the gate waits for the user instead of counting.
 */
export function useCountdown(): { state: "counting" | "held" | "waiting" | "closed"; remainingMs: number; totalMs: number } {
  const gate = useVerityStore((s) => s.snapshot.run.gate);
  const clock = useVerityStore((s) => s.tMs);
  const autopilot = useVerityStore((s) => s.autopilot);

  if (gate.state === "closed") return { state: "closed", remainingMs: 0, totalMs: 0 };
  const totalMs = gate.autoContinueMs;
  if (gate.state === "held") return { state: "held", remainingMs: totalMs, totalMs };
  if (!autopilot) return { state: "waiting", remainingMs: totalMs, totalMs };
  const remainingMs = Math.max(0, Math.min(totalMs, totalMs - (clock - gate.openedAtMs)));
  return { state: "counting", remainingMs, totalMs };
}
