import type { StateCreator } from "zustand";
import { PRESETS, type KnobValue } from "@/lib/contracts/presets";
import type { Priority, RunEvent } from "@/lib/contracts/types";
import type { LiveConnection, SteerRequest } from "@/lib/contracts/view-types";
import type { StartFailureReason } from "@/lib/sources/live";
import type { Store } from "../types";

/** Compatible with the contract type; `reason` lets the Try page pick the busy or budget message. */
export type StartLiveResult =
  | { runId: string }
  | { error: string; reason?: StartFailureReason };

const SESSION_EXPIRED = "Your session expired. Open the Try page and enter the access code again.";

export type LiveSlice = {
  connection: LiveConnection;
  runId: string | null;
  error: string | null;
  pendingSteers: SteerRequest[];
  /**
   * The run has ended but its stream is still open: the server may send a few more events after the end (the sign-off
   * checks run after the result shows). False once the stream closes.
   */
  tailOpen: boolean;
  setTailOpen(open: boolean): void;
  /** `params` and `title` come from the bench; `params` is sent only when the agent supports knobs. `researchId` is the check made before Run. `threadId` is the chat thread the run starts from. */
  startLive(presetId: string, priority: Priority, params?: Record<string, KnobValue>, title?: string, researchId?: string, threadId?: string): Promise<StartLiveResult>;
  steer(req: SteerRequest): Promise<void>;
  stopRun(): Promise<void>;
  /** Replay only: a local human + winner event pair through the reducer. */
  overrideWinnerLocal(candidateId: string): void;
  setConnection(c: LiveConnection, error?: string | null): void;
  /** Live events arrive here: logged for the zip export, applied through the one reducer, echoes (and a closed gate) clear pending steers. */
  ingestLive(e: RunEvent): void;
  /** Begins listening to a live run that was not started from this page (fresh load of /studio?run=id). */
  adoptLiveRun(runId: string): void;
};

function sameSteer(a: SteerRequest, b: SteerRequest): boolean {
  if (a.kind !== b.kind) return false;
  switch (a.kind) {
    case "pin":
      return b.kind === "pin" && a.blockId === b.blockId && a.pin === b.pin;
    case "toggle":
      return b.kind === "toggle" && a.blockId === b.blockId && a.variantId === b.variantId && a.value === b.value;
    case "winner_override":
      return b.kind === "winner_override" && a.candidateId === b.candidateId;
    default:
      return false;
  }
}

/** Only changes the server echoes as a `human` event are tracked as pending. */
function isTrackedSteer(req: SteerRequest): boolean {
  return req.kind === "pin" || req.kind === "toggle" || req.kind === "winner_override";
}

export const createLiveSlice: StateCreator<Store, [], [], LiveSlice> = (set, get) => ({
  connection: "idle",
  runId: null,
  error: null,
  pendingSteers: [],
  tailOpen: false,

  setTailOpen(open) {
    if (get().tailOpen !== open) set({ tailOpen: open });
  },

  async startLive(presetId, priority, params, title, researchId, threadId) {
    // Dynamic import: the replay path never loads the module that can reach the agent.
    const live = await import("@/lib/sources/live");
    set({ connection: "connecting", error: null });
    const res = await live.postRun(presetId, priority, params, researchId, threadId);
    if ("error" in res) {
      set({ connection: "idle", error: res.error });
      return res;
    }
    const preset = PRESETS.find((p) => p.id === presetId);
    get().resetRun({ runId: res.runId, mode: "live", sample: false, title: title ?? preset?.title ?? "Live run" });
    set({
      source: "live",
      status: "playing",
      tMs: 0,
      durationMs: 0,
      log: [],
      logMeta: null,
      src: null,
      cursor: 0,
      runId: res.runId,
      connection: "connecting",
      pendingSteers: [],
      tailOpen: false,
      error: null,
    });
    // Never a blank canvas: place the preset's spec node before the first server event. Custom knobs change the spec,
    // so the preset text is seeded only when no knobs were sent; the agent's own spec event follows either way.
    if (preset && !params) {
      get().applyEvents([
        { seq: 0, tMs: 0, t: "spec", spec: { ...preset.spec, priority } },
        { seq: 0, tMs: 0, t: "op", by: "ai", op: { op: "add_node", id: "spec", kind: "spec", zone: "plan", label: "Spec", refId: "spec" } },
        { seq: 0, tMs: 0, t: "credits", usedUsd: 0, capUsd: res.capUsd },
      ]);
    }
    return { runId: res.runId };
  },

  async steer(req) {
    if (req.kind === "autopilot") get().setAutopilot(req.on);
    const { source, runId } = get();
    if (source !== "live" || !runId) return;
    const tracked = isTrackedSteer(req);
    if (tracked) set({ pendingSteers: [...get().pendingSteers, req] });
    const live = await import("@/lib/sources/live");
    const result = await live.postSteer(runId, req);
    if (result !== "ok") {
      set({
        pendingSteers: get().pendingSteers.filter((p) => p !== req),
        error: result === "unauthorized" ? SESSION_EXPIRED : "Could not send your change. The run keeps going.",
      });
    }
  },

  async stopRun() {
    const { source, runId } = get();
    if (source !== "live" || !runId) return;
    const live = await import("@/lib/sources/live");
    const result = await live.postStop(runId);
    if (result === "unauthorized") set({ error: SESSION_EXPIRED });
    else if (result !== "ok") set({ error: "Could not stop the run. Try again." });
  },

  overrideWinnerLocal(candidateId) {
    const { snapshot } = get();
    const run = snapshot.run;
    const tMs = run.tMs;
    const aiPickId = run.aiWinnerId ?? run.winnerId ?? candidateId;
    get().applyEvents([
      { seq: run.lastSeq, tMs, t: "human", input: { kind: "winner_override", tMs, candidateId, local: true } },
      { seq: run.lastSeq, tMs, t: "winner", candidateId, by: "user", aiPickId },
    ]);
  },

  setConnection(c, error) {
    set(error === undefined ? { connection: c } : { connection: c, error });
  },

  ingestLive(e) {
    const st = get();
    st.applyEvents([e]);
    const patch: Partial<Store> = { log: [...st.log, e] };
    // Gate steers (build_now, hold, resume, autopilot) are never tracked as pending: the backend answers them with a gate
    // event, not a human echo. A pin or toggle the backend ignored (unknown block or option) never gets an echo either, so
    // anything still pending when the gate closes, or the run ends, can no longer be answered.
    const gateClosed = e.t === "gate" && e.state === "closed";
    const runEnded = e.t === "run" && (e.phase === "done" || e.phase === "error" || e.phase === "cap_reached" || e.phase === "stopped");
    if (gateClosed || runEnded) patch.pendingSteers = [];
    if (e.t === "human") {
      const i = st.pendingSteers.findIndex((p) => {
        const input = e.input;
        switch (input.kind) {
          case "pin":
            return sameSteer(p, { kind: "pin", blockId: input.blockId, pin: input.pin });
          case "toggle":
            return sameSteer(p, { kind: "toggle", blockId: input.blockId, variantId: input.variantId, value: input.value });
          case "winner_override":
            return sameSteer(p, { kind: "winner_override", candidateId: input.candidateId });
          default:
            return false;
        }
      });
      if (i >= 0) patch.pendingSteers = st.pendingSteers.filter((_, j) => j !== i);
    }
    if (e.t === "run" && e.message) patch.error = e.message;
    set(patch);
  },

  adoptLiveRun(runId) {
    get().resetRun({ runId, mode: "live", sample: false, title: "Live run" });
    set({
      source: "live",
      status: "playing",
      tMs: 0,
      durationMs: 0,
      log: [],
      logMeta: null,
      src: null,
      cursor: 0,
      runId,
      connection: "connecting",
      pendingSteers: [],
      tailOpen: false,
      error: null,
    });
  },
});
