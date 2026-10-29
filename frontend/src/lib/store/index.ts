"use client";

import { useMemo } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { KnobValue } from "@/lib/contracts/presets";
import type { Chapter, Priority, RunState } from "@/lib/contracts/types";
import type {
  InspectorTab,
  LiveConnection,
  PlaybackStatus,
  Selection,
  SteerRequest,
} from "@/lib/contracts/view-types";
import { createLiveSlice, type StartLiveResult } from "./slices/live";
import { createPlaybackSlice } from "./slices/playback";
import { createRunSlice } from "./slices/run";
import { createUiSlice } from "./slices/ui";
import type { Store } from "./types";

/**
 * One module-level store (slices pattern). Safe because the app is a static client SPA with no per-request
 * server state; it also lets the Landing hero's playback position carry into /studio on client navigation.
 */
export const useVerityStore = create<Store>()((...a) => ({
  ...createRunSlice(...a),
  ...createPlaybackSlice(...a),
  ...createUiSlice(...a),
  ...createLiveSlice(...a),
}));

/**
 * Typed read of the run state. The selector is re-evaluated whenever the run changes (once per event, not per
 * frame), so it may safely return fresh arrays or objects without causing render loops.
 */
export function useRun<T>(selector: (run: RunState) => T): T {
  const run = useVerityStore((s) => s.snapshot.run);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- selectors are usually inline; the run identity is the real input
  return useMemo(() => selector(run), [run]);
}

export function usePlayback(): {
  status: PlaybackStatus;
  tMs: number;
  durationMs: number;
  speed: 1 | 2;
  chapters: Chapter[];
  play(): void;
  pause(): void;
  seek(tMs: number): void;
  restart(): void;
  setSpeed(s: 1 | 2): void;
} {
  return useVerityStore(
    useShallow((s) => ({
      status: s.status,
      tMs: s.tMs,
      durationMs: s.durationMs,
      speed: s.speed,
      chapters: s.chapters,
      play: s.play,
      pause: s.pause,
      seek: s.seek,
      restart: s.restart,
      setSpeed: s.setSpeed,
    })),
  );
}

export function useUi(): {
  selection: Selection | null;
  inspectorTab: InspectorTab;
  autopilot: boolean;
  instant: boolean;
  select(sel: Selection | null): void;
  setTab(t: InspectorTab): void;
  setAutopilot(on: boolean): void;
  /** Extras for narrow screens: the inspector Sheet. */
  inspectorOpen: boolean;
  setInspectorOpen(open: boolean): void;
} {
  return useVerityStore(
    useShallow((s) => ({
      selection: s.selection,
      inspectorTab: s.inspectorTab,
      autopilot: s.autopilot,
      instant: s.instant,
      select: s.select,
      setTab: s.setTab,
      setAutopilot: s.setAutopilot,
      inspectorOpen: s.inspectorOpen,
      setInspectorOpen: s.setInspectorOpen,
    })),
  );
}

export function useLive(): {
  connection: LiveConnection;
  runId: string | null;
  error: string | null;
  pendingSteers: SteerRequest[];
  startLive(presetId: string, priority: Priority, params?: Record<string, KnobValue>, title?: string, researchId?: string, threadId?: string): Promise<StartLiveResult>;
  steer(req: SteerRequest): Promise<void>;
  stopRun(): Promise<void>;
  overrideWinnerLocal(candidateId: string): void;
} {
  return useVerityStore(
    useShallow((s) => ({
      connection: s.connection,
      runId: s.runId,
      error: s.error,
      pendingSteers: s.pendingSteers,
      startLive: s.startLive,
      steer: s.steer,
      stopRun: s.stopRun,
      overrideWinnerLocal: s.overrideWinnerLocal,
    })),
  );
}
