import type { StateCreator } from "zustand";
import type { RunEvent } from "@/lib/contracts/types";
import type { VerityNode } from "@/lib/canvas/zones";
import { initialSnapshot, reduce, type RunSnapshot } from "@/lib/run/reducer";
import type { Store } from "../types";

export type RunInit = { runId: string; mode: "replay" | "live"; sample: boolean; title: string };

export type RunSlice = {
  snapshot: RunSnapshot;
  /** Positions the user gave to nodes. Re-applied after every event and every rewind. */
  userMoved: Record<string, { x: number; y: number }>;
  applyEvent(e: RunEvent): void;
  applyEvents(es: RunEvent[]): void;
  resetRun(init: RunInit): void;
};

/** Pure: re-applies the user's positions on top of a snapshot (the AI never moves a user-moved node). */
export function withUserMoves(snap: RunSnapshot, moved: RunSlice["userMoved"]): RunSnapshot {
  const ids = Object.keys(moved);
  if (ids.length === 0) return snap;
  let changed = false;
  const nodes: VerityNode[] = snap.nodes.map((n) => {
    const m = moved[n.id];
    if (!m) return n;
    if (n.data.origin === "user" && n.position.x === m.x && n.position.y === m.y) return n;
    changed = true;
    return { ...n, position: { x: m.x, y: m.y }, data: { ...n.data, origin: "user" } };
  });
  return changed ? { ...snap, nodes } : snap;
}

export function foldEvents(snap: RunSnapshot, events: RunEvent[], moved: RunSlice["userMoved"]): RunSnapshot {
  let s = snap;
  for (const e of events) s = withUserMoves(reduce(s, e), moved);
  return s;
}

export const createRunSlice: StateCreator<Store, [], [], RunSlice> = (set, get) => ({
  snapshot: initialSnapshot({ runId: "", mode: "replay", sample: false, title: "" }),
  userMoved: {},

  applyEvent(e) {
    get().applyEvents([e]);
  },

  applyEvents(es) {
    if (es.length === 0) return;
    const { snapshot, userMoved, selection } = get();
    const next = foldEvents(snapshot, es, userMoved);
    // A removed node cannot stay selected.
    let clearSelection = false;
    if (selection) {
      for (const e of es) {
        if (e.t === "op" && e.op.op === "remove") {
          const gone = snapshot.nodes.find((n) => n.id === (e.op as { id: string }).id);
          if (gone && gone.data.refId === selection.id) clearSelection = true;
        }
      }
    }
    set(clearSelection ? { snapshot: next, selection: null } : { snapshot: next });
  },

  resetRun(init) {
    set({ snapshot: initialSnapshot(init), userMoved: {}, selection: null, seenDiffs: {} });
  },
});
