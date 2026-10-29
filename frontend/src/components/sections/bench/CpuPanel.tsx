"use client";

import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react";
import { ArrowLeft, PanelRightClose, X } from "lucide-react";
import { CpuBoard } from "./CpuBoard";
import { CpuRunView } from "./CpuRunView";

type View = "board" | { runId: string; title: string };

/**
 * The right panel for one CPU job: the live board beside the chat, so the chat stays a conversation. Same container and
 * header as the run panel (OutputPanel). BenchView decides when it is open; the close button only asks to hide it.
 * A block, core or version run opens inside the panel (CpuRunView) with a Back button, so the chat and the page stay put.
 */
export function CpuPanel({
  id,
  onClose,
  focusOnOpen,
  panelRef,
}: {
  id: string;
  onClose(): void;
  /** Opened by the user (Show board, the top-bar button) and not by the page itself: focus moves into the panel. */
  focusOnOpen: boolean;
  panelRef: RefObject<HTMLElement | null>;
}) {
  const uid = useId();
  const headRef = useRef<HTMLHeadingElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const boardScroll = useRef(0);
  // Another CPU job (?cpuJob=) is a different board: the view belongs to the job it was opened on.
  const [state, setState] = useState<{ jobId: string; view: View }>({ jobId: id, view: "board" });
  if (state.jobId !== id) setState({ jobId: id, view: "board" }); // so coming back to the first job does not reopen its run
  const view: View = state.jobId === id ? state.view : "board";
  const prev = useRef<{ jobId: string; view: View }>({ jobId: id, view: "board" });

  useEffect(() => {
    if (focusOnOpen) headRef.current?.focus();
  }, [focusOnOpen]);

  // Entering a run puts focus on its heading and starts it at the top. Going back restores the board's scroll position
  // and returns focus to the control that opened the run (the board heading if that control is gone).
  useEffect(() => {
    const was = prev.current;
    prev.current = { jobId: id, view };
    if (was.view === view) return;
    if (was.jobId !== id) {
      // Another job replaced the run view: not a Back, so no old scroll or opener. Focus stays put unless it left with the run view.
      opener.current = null;
      if (!document.activeElement || document.activeElement === document.body) headRef.current?.focus({ preventScroll: true });
      return;
    }
    const body = bodyRef.current;
    if (view !== "board") {
      if (body) body.scrollTop = 0;
      headRef.current?.focus();
    } else {
      if (body) body.scrollTop = boardScroll.current;
      const from = opener.current;
      opener.current = null;
      (from && from.isConnected ? from : headRef.current)?.focus({ preventScroll: true });
    }
  }, [id, view]);

  const openRun = useCallback((runId: string, title: string) => {
    const body = bodyRef.current;
    const active = document.activeElement;
    boardScroll.current = body?.scrollTop ?? 0;
    // A mouse click does not focus a link in every browser; then Back falls back to the panel heading.
    opener.current = active instanceof HTMLElement && active !== body && body?.contains(active) ? active : null;
    setState({ jobId: id, view: { runId, title } });
  }, [id]);

  return (
    <aside className="b-panel" id="bench-panel" aria-labelledby={`${uid}-h`} ref={panelRef}>
      <div className="b-panel-h">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          {view !== "board" ? (
            <button type="button" className="bx ghost sm" onClick={() => setState({ jobId: id, view: "board" })}>
              <ArrowLeft aria-hidden className="ico" />
              Back to CPU board
            </button>
          ) : null}
          <h2 id={`${uid}-h`} ref={headRef} tabIndex={-1} className="b-panel-t">
            {view === "board" ? "CPU job" : view.title}
          </h2>
        </div>
        <div className="b-panel-acts">
          <button type="button" className="b-ib" aria-label="Close the panel" onClick={onClose}>
            <X aria-hidden className="b-ico-n" />
            <PanelRightClose aria-hidden className="b-ico-w" />
          </button>
        </div>
      </div>
      <div className="b-panel-b" tabIndex={0} role="region" aria-label={view === "board" ? "CPU job" : `Run: ${view.title}`} ref={bodyRef}>
        {/* The board stays mounted while a run is open, so its scroll position and polling carry on behind the run view. */}
        <div hidden={view !== "board"}>
          <CpuBoard key={id} id={id} onOpenRun={openRun} />
        </div>
        {view !== "board" ? <CpuRunView key={view.runId} jobId={id} runId={view.runId} title={view.title} /> : null}
      </div>
    </aside>
  );
}
