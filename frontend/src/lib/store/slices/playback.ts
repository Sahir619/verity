import type { StateCreator } from "zustand";
import type { Chapter, RunEvent, RunLog } from "@/lib/contracts/types";
import type { PlaybackStatus } from "@/lib/contracts/view-types";
import { initialSnapshot } from "@/lib/run/reducer";
import type { Store } from "../types";
import { foldEvents } from "./run";
import { clearInstantSoon } from "./ui";

export type PlaybackSlice = {
  source: "replay" | "live" | null;
  status: PlaybackStatus;
  /** The clock: replay position, or elapsed live time. */
  tMs: number;
  durationMs: number;
  speed: 1 | 2;
  chapters: Chapter[];
  /** Replay: the recorded events. Live: every event received so far (used by the zip export). */
  log: RunEvent[];
  logMeta: RunLog["meta"] | null;
  /** The URL the replay was loaded from. Lets a second driver (Landing then Studio) continue instead of reloading. */
  src: string | null;
  cursor: number;
  /** Bumped by restart() after a failed load so the driver tries again. */
  reloadKey: number;
  load(log: RunLog, src?: string): void;
  /** Driver hooks: a fetch is starting or failed. */
  beginReplayLoad(): void;
  failReplayLoad(message: string): void;
  play(): void;
  pause(): void;
  seek(tMs: number): void;
  restart(): void;
  setSpeed(s: 1 | 2): void;
  tick(dtMs: number): void;
  /** Live and error paths. */
  setStatus(s: PlaybackStatus): void;
  setClock(tMs: number): void;
};

/** A forward jump longer than this applies instantly (no enter animations). */
const INSTANT_JUMP_MS = 1000;

export const createPlaybackSlice: StateCreator<Store, [], [], PlaybackSlice> = (set, get) => {
  /** Moves the replay clock to `target`, rebuilding from scratch when `rewind` is true. One store write. */
  function advanceTo(target: number, rewind: boolean, instant: boolean) {
    const st = get();
    const meta = st.logMeta;
    if (!meta) return;
    let snapshot = st.snapshot;
    let cursor = st.cursor;
    if (rewind) {
      snapshot = initialSnapshot({ runId: meta.runId, mode: "replay", sample: meta.sample, title: meta.title });
      cursor = 0;
    }
    const due: RunEvent[] = [];
    while (cursor < st.log.length && st.log[cursor].tMs <= target) {
      due.push(st.log[cursor]);
      cursor++;
    }
    snapshot = foldEvents(snapshot, due, st.userMoved);
    // A removed or rewound selection must not point at nothing.
    let selection = st.selection;
    if (rewind && selection && !snapshot.nodes.some((n) => n.data.refId === selection!.id && n.data.kind === selection!.kind)) {
      selection = null;
    }
    set({
      snapshot,
      cursor,
      tMs: target,
      selection,
      ...(instant ? { instant: true } : {}),
      ...(selection === null && st.selection !== null ? { inspectorOpen: false } : {}),
    });
    if (instant) clearInstantSoon(set);
  }

  return {
    source: null,
    status: "loading",
    tMs: 0,
    durationMs: 0,
    speed: 1,
    chapters: [],
    log: [],
    logMeta: null,
    src: null,
    cursor: 0,
    reloadKey: 0,

    beginReplayLoad() {
      const st = get();
      if (st.source === "replay" && st.status === "error") {
        set({ status: "loading" });
        return;
      }
      get().resetRun({ runId: "", mode: "replay", sample: false, title: "" });
      set({
        source: "replay",
        status: "loading",
        tMs: 0,
        durationMs: 0,
        chapters: [],
        log: [],
        logMeta: null,
        src: null,
        cursor: 0,
        inspectorOpen: false,
      });
    },

    failReplayLoad(message) {
      if (process.env.NODE_ENV !== "production") console.warn("[verity] replay load failed:", message);
      set({ status: "error" });
    },

    load(log, src) {
      const snapshot = initialSnapshot({
        runId: log.meta.runId,
        mode: "replay",
        sample: log.meta.sample,
        title: log.meta.title,
      });
      set({
        snapshot,
        userMoved: {},
        selection: null,
        seenDiffs: {},
        inspectorOpen: false,
        source: "replay",
        status: "ready",
        tMs: 0,
        durationMs: log.meta.durationMs,
        chapters: log.meta.chapters,
        log: log.events,
        logMeta: log.meta,
        src: src ?? null,
        cursor: 0,
      });
      // The first frame is never blank: events at 0 ms (the spec node) apply immediately.
      advanceTo(0, false, false);
    },

    play() {
      const { status, tMs, durationMs } = get();
      if (status === "ended" || (durationMs > 0 && tMs >= durationMs)) {
        get().restart();
        return;
      }
      if (status === "ready" || status === "paused") set({ status: "playing" });
    },

    pause() {
      if (get().status === "playing") set({ status: "paused" });
    },

    seek(tMs) {
      const st = get();
      if (st.source !== "replay" || !st.logMeta) return;
      const target = Math.max(0, Math.min(st.durationMs, tMs));
      const rewind = target < st.tMs;
      const instant = rewind || target - st.tMs > INSTANT_JUMP_MS;
      advanceTo(target, rewind, instant);
      const atEnd = target >= st.durationMs;
      if (atEnd && st.status === "playing") set({ status: "ended" });
      else if (!atEnd && st.status === "ended") set({ status: "paused" });
    },

    restart() {
      const st = get();
      if (st.status === "error" && st.source === "replay") {
        set({ reloadKey: st.reloadKey + 1 });
        return;
      }
      if (st.source !== "replay" || !st.logMeta) return;
      advanceTo(0, true, true);
      set({ status: "playing" });
    },

    setSpeed(s) {
      set({ speed: s });
    },

    tick(dtMs) {
      const st = get();
      if (st.status !== "playing" || st.source !== "replay") return;
      const target = Math.min(st.durationMs, st.tMs + dtMs * st.speed);
      advanceTo(target, false, false);
      if (target >= st.durationMs) set({ status: "ended" });
    },

    setStatus(s) {
      set({ status: s });
    },

    setClock(tMs) {
      set({ tMs });
    },
  };
};
