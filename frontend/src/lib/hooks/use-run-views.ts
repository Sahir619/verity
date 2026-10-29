"use client";

import { useMemo } from "react";
import { selectEntity, selectRunSummary, selectStageViews } from "@/lib/run/selectors";
import { useVerityStore } from "@/lib/store";
import type { RunSummary, SelectedEntity, StageView } from "@/lib/contracts/view-types";

/** Resolves the UI selection against the run. */
export function useSelectedEntity(): SelectedEntity | null {
  const run = useVerityStore((s) => s.snapshot.run);
  const selection = useVerityStore((s) => s.selection);
  return useMemo(() => selectEntity(run, selection), [run, selection]);
}

export function useStageViews(): StageView[] {
  const run = useVerityStore((s) => s.snapshot.run);
  return useMemo(() => selectStageViews(run), [run]);
}

export function useRunSummary(): RunSummary {
  const run = useVerityStore((s) => s.snapshot.run);
  const nodes = useVerityStore((s) => s.snapshot.nodes);
  return useMemo(() => {
    const hidden = new Set(nodes.filter((n) => n.hidden && n.data.kind === "candidate").map((n) => n.data.refId));
    return selectRunSummary(run, hidden);
  }, [run, nodes]);
}
