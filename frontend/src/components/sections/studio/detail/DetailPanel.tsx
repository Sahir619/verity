"use client";

import { useMemo, useRef } from "react";
import type { RunState } from "@/lib/contracts/types";
import type { SelectedEntity, Selection } from "@/lib/contracts/view-types";
import { selectEntity } from "@/lib/run/selectors";
import { useUi } from "@/lib/store";
import { CandidateDetail } from "./CandidateDetail";
import { BlockDetail, GoldenDetail, SpecDetail } from "./ItemDetails";

function entityKey(e: SelectedEntity): string {
  switch (e.kind) {
    case "candidate":
      return `candidate:${e.candidate.id}`;
    case "block":
      return `block:${e.block.id}`;
    default:
      return e.kind;
  }
}

function Body({ entity, run }: { entity: SelectedEntity; run: RunState }) {
  switch (entity.kind) {
    case "candidate":
      return <CandidateDetail entity={entity} run={run} />;
    case "spec":
      return <SpecDetail spec={entity.spec} />;
    case "golden":
      return <GoldenDetail entity={entity} />;
    case "block":
      return <BlockDetail entity={entity} run={run} />;
  }
}

/**
 * The detail panel. By default it follows the run (the latest change); clicking a figure box or a table row pins
 * it through the store's single selection (ui.select), and "Follow run" clears the selection to hand control back.
 */
export function DetailPanel({ run, follow }: { run: RunState; follow: Selection | null }) {
  const { selection, select } = useUi();
  const pinned = useMemo(() => selectEntity(run, selection), [run, selection]);
  const followed = useMemo(() => selectEntity(run, follow), [run, follow]);
  const isPinned = selection !== null;
  const entity = isPinned && pinned ? pinned : followed;
  const panelRef = useRef<HTMLElement>(null);

  const followRun = () => {
    select(null);
    // The button unmounts once the panel follows again; keep keyboard focus in the panel instead of <body>.
    panelRef.current?.focus();
  };

  return (
    <aside ref={panelRef} className="s-detail dp" aria-label="Detail" tabIndex={-1}>
      <div className="dp-bar">
        {isPinned ? (
          <>
            <span className="dp-mode">{pinned ? "Pinned" : "Pinned item is not in the run yet"}</span>
            <button type="button" className="btn ghost xs" onClick={followRun}>
              Follow run
            </button>
          </>
        ) : (
          <span className="dp-mode dp-live">
            <span className="sq" aria-hidden />
            Following the run
          </span>
        )}
      </div>
      <div className="dp-body" tabIndex={0} aria-label="Detail, scrollable" key={entity ? entityKey(entity) : "none"}>
        {entity ? (
          <Body entity={entity} run={run} />
        ) : (
          <div className="dp-hero">
            <h2 className="dp-title">
              <span className="lbl">Detail</span>
            </h2>
            <p className="dp-reason">Each step of the run appears here as it happens. Click any box or table row to pin it.</p>
            <ul className="dp-legend" aria-label="Markers">
              <li>
                <span className="mk pass">Pass</span> passed the locked tests
              </li>
              <li>
                <span className="mk fail">Fail</span> failed a locked test
              </li>
              <li>
                <span className="mk win">Done</span> a finished step
              </li>
            </ul>
          </div>
        )}
      </div>
    </aside>
  );
}
