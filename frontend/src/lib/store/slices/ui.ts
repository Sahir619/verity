import type { StateCreator } from "zustand";
import type { InspectorTab, Selection } from "@/lib/contracts/view-types";
import type { Store } from "../types";

export type UiSlice = {
  selection: Selection | null;
  inspectorTab: InspectorTab;
  autopilot: boolean;
  /** Suppresses enter animations during seeks. */
  instant: boolean;
  inspectorOpen: boolean;
  /** Versions whose diff the user has opened (clears the diff dot on the node). */
  seenDiffs: Record<string, true>;
  select(sel: Selection | null): void;
  setTab(t: InspectorTab): void;
  setAutopilot(on: boolean): void;
  setInspectorOpen(open: boolean): void;
  setInstant(on: boolean): void;
};

/** Flips `instant` off after the nodes of a seek have mounted (two frames). */
export function clearInstantSoon(set: (p: Partial<Store>) => void): void {
  if (typeof requestAnimationFrame === "undefined") {
    set({ instant: false });
    return;
  }
  requestAnimationFrame(() => requestAnimationFrame(() => set({ instant: false })));
}

export const createUiSlice: StateCreator<Store, [], [], UiSlice> = (set, get) => ({
  selection: null,
  inspectorTab: "rtl",
  autopilot: true,
  instant: false,
  inspectorOpen: false,
  seenDiffs: {},

  select(sel) {
    const cur = get().selection;
    if (cur === sel || (cur && sel && cur.kind === sel.kind && cur.id === sel.id)) {
      if (sel === null && get().inspectorOpen) set({ inspectorOpen: false });
      return;
    }
    const seen = sel && sel.kind === "candidate" ? { ...get().seenDiffs, [sel.id]: true as const } : get().seenDiffs;
    set({ selection: sel, inspectorOpen: sel !== null, seenDiffs: seen });
  },
  setTab(t) {
    set({ inspectorTab: t });
  },
  setAutopilot(on) {
    set({ autopilot: on });
  },
  setInspectorOpen(open) {
    set({ inspectorOpen: open });
  },
  setInstant(on) {
    set({ instant: on });
  },
});
