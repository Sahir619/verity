"use client";

import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { Download, PanelRightClose, X } from "lucide-react";
import type { Equivalence, PPA, Priority, RunState } from "@/lib/contracts/types";
import type { LiveConnection, Selection } from "@/lib/contracts/view-types";
import { useUi } from "@/lib/store";
import { useRunZip } from "../shared/use-run-zip";
import { CharacteristicsTable } from "../studio/table/CharacteristicsTable";
import { TERMINAL, currentCandidate } from "./derive";
import { ProgressTab } from "./ProgressTab";
import { ResultModule } from "./ResultModule";
import { RtlModule } from "./RtlModule";

export type PanelTab = "progress" | "result" | "versions" | "verilog" | "architecture" | "version";

const TABS: { id: PanelTab; label: string }[] = [
  { id: "progress", label: "Progress" },
  { id: "result", label: "Result" },
  { id: "versions", label: "Versions" },
  { id: "verilog", label: "Verilog" },
  { id: "architecture", label: "Architecture" },
];

/**
 * The right panel, after the Claude desktop Progress box. BenchView opens it on Progress when a run starts (900px and
 * up) and moves it to Result when the run is done; Show progress and the result card in the chat reopen it. Five tabs
 * over parts that already read the run (the live steps, the result, the versions table, the Verilog and the
 * architecture), plus one tab for the version picked last, titled by its name ("v4"). Below 900px it is a full-screen sheet.
 */
export function OutputPanel({
  run,
  title,
  tab,
  onTab,
  onClose,
  follow,
  human,
  humanFeature,
  priority,
  connection,
  liveError,
  stopping,
  onStop,
  onReconnect,
  focusOnOpen,
  panelRef,
  onPickVersion,
  versionLabel,
  versionTab,
  architecture,
  signoffLine,
}: {
  run: RunState;
  title: string;
  tab: PanelTab;
  onTab(tab: PanelTab): void;
  onClose(): void;
  follow: Selection | null;
  human: { ppa: PPA; eqy: Equivalence } | null;
  humanFeature: boolean;
  priority: Priority;
  connection: LiveConnection;
  liveError: string | null;
  stopping: boolean;
  onStop(): void;
  onReconnect(): void;
  /** Opened by the user (not by the run ending): focus moves into the panel. */
  focusOnOpen: boolean;
  panelRef: RefObject<HTMLElement | null>;
  /** A version picked in this panel. `open`: show its own tab (a Versions row); a picker keeps the tab it is on. */
  onPickVersion(id: string, open: boolean): void;
  /** The name of the version picked last ("v4"), which titles its tab; null when none is picked. */
  versionLabel: string | null;
  versionTab: ReactNode;
  architecture: ReactNode;
  /** The sign-off sheet in one line, for the Result tab, or what stands in for it. */
  signoffLine: ReactNode;
}) {
  const id = useId();
  const headRef = useRef<HTMLHeadingElement>(null);
  const tabRefs = useRef<Record<PanelTab, HTMLButtonElement | null>>({ progress: null, result: null, versions: null, verilog: null, architecture: null, version: null });
  const tabs = versionLabel ? [...TABS, { id: "version" as const, label: versionLabel }] : TABS;
  const zip = useRunZip();
  const { selection } = useUi();
  const ended = TERMINAL.has(run.phase);
  // An ended run has no version "in progress" to follow: the one you picked, else the winner, else v1, else the latest.
  const pinned = selection?.kind === "candidate" ? (run.candidates[selection.id] ?? null) : null;
  const settledId = run.winnerId ?? run.baselineId;
  const settled = ended && settledId ? (run.candidates[settledId] ?? null) : null;
  const current = pinned ?? settled ?? currentCandidate(run, selection, follow);
  // Versions the run left half done (a stop, the cap, an error): never described as still being worked on.
  const unfinished = ended && run.candidateOrder.some((cid) => {
    const st = run.candidates[cid]?.status;
    return st === "drafting" || st === "linting" || st === "simulating" || st === "synthesizing";
  });

  useEffect(() => {
    if (focusOnOpen) headRef.current?.focus();
  }, [focusOnOpen]);

  // The tab in view stays in view when the strip scrolls sideways (a narrow panel).
  useEffect(() => {
    tabRefs.current[tab]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab, versionLabel]);

  // Arrow keys move between the tabs (the WAI-ARIA tabs pattern); Home and End jump to the ends.
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.findIndex((t) => t.id === tab);
    const next =
      e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length] : e.key === "ArrowLeft" ? tabs[(i + tabs.length - 1) % tabs.length] : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    onTab(next.id);
    tabRefs.current[next.id]?.focus();
  };

  return (
    <aside className="b-panel" id="bench-panel" aria-labelledby={`${id}-h`} ref={panelRef}>
      <div className="b-panel-h">
        <h2 id={`${id}-h`} ref={headRef} tabIndex={-1} className="b-panel-t">
          {title}
        </h2>
        <div className="b-panel-acts">
          {ended ? (
            <button type="button" className="bx ghost sm" onClick={() => void zip.download()} disabled={zip.state === "busy"}>
              <Download aria-hidden className="ico" />
              {zip.state === "busy" ? "Preparing..." : "Download"}
            </button>
          ) : null}
          <button type="button" className="b-ib" aria-label="Close the panel" onClick={onClose}>
            <X aria-hidden className="b-ico-n" />
            <PanelRightClose aria-hidden className="b-ico-w" />
          </button>
        </div>
      </div>
      {zip.state === "error" ? (
        <p className="b-note b-panel-err" role="alert">
          The download could not be built. Try again.
        </p>
      ) : null}
      <div className="b-tabs" role="tablist" aria-label="Run views" onKeyDown={onTabKey}>
        {tabs.map((t) => (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[t.id] = el;
            }}
            type="button"
            role="tab"
            id={`${id}-t-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`${id}-p`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="b-panel-b" role="tabpanel" id={`${id}-p`} aria-labelledby={`${id}-t-${tab}`} tabIndex={0}>
        {tab === "progress" ? (
          <ProgressTab
            run={run}
            priority={priority}
            connection={connection}
            liveError={liveError}
            stopping={stopping}
            onStop={onStop}
            onReconnect={onReconnect}
          />
        ) : tab === "result" ? (
          ended ? (
            <ResultModule run={run} hasRun human={human} humanFeature={humanFeature} priority={priority} signoff={signoffLine} />
          ) : (
            <p className="b-empty">The result appears here when the run is done.</p>
          )
        ) : tab === "versions" ? (
          <div className="b-versions" data-ended={ended ? "" : undefined} data-nobase={ended && !run.baselineId ? "" : undefined}>
            {ended && run.candidateOrder.length === 0 ? (
              <p className="b-empty">No version was written before the run ended.</p>
            ) : (
              <>
                <p className="b-note b-dim">Every version the agent wrote, measured against our own v1. Pick one to open it in its own tab and ask about it.</p>
                {unfinished ? <p className="b-note">The run ended before these versions were tested.</p> : null}
                <CharacteristicsTable run={run} follow={follow} onPick={(cid) => onPickVersion(cid, true)} />
              </>
            )}
          </div>
        ) : tab === "architecture" ? (
          architecture
        ) : tab === "version" ? (
          versionTab
        ) : (
          <div className="b-verilog">
            {run.candidateOrder.length > 1 ? (
              <label className="b-pick">
                <span>Version</span>
                <select value={current?.id ?? ""} onChange={(e) => onPickVersion(e.target.value, false)}>
                  {run.candidateOrder.map((cid) => {
                    const c = run.candidates[cid];
                    return c ? (
                      <option key={cid} value={cid}>
                        {c.label}
                        {cid === run.winnerId ? " (winner)" : ""}
                      </option>
                    ) : null;
                  })}
                </select>
              </label>
            ) : null}
            <RtlModule candidate={current} isWinner={current !== null && current.id === run.winnerId} ended={ended} />
          </div>
        )}
      </div>
    </aside>
  );
}
