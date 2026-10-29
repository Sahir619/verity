"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";
import { EmptyState } from "@/components/domain/EmptyState";
import { VignetteGrid } from "@/components/sections/shared/VignetteGrid";
import { useLive, useRun, useUi, useVerityStore } from "@/lib/store";
import { Mod } from "./chrome/Mod";
import { RunMeta, StudioBand } from "./chrome/StudioBand";
import { DetailPanel } from "./detail/DetailPanel";
import { useFollowFocus } from "./detail/follow";
import { LoopModule } from "./loop/LoopModule";
import { STUDIO_ROOT } from "./studio-root";
import { CharacteristicsTable } from "./table/CharacteristicsTable";
import { NarrationFor, TimelineBar } from "./timeline/TimelineBar";

function isTextField(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/** Controls that already use Space or the arrow keys for themselves. */
function ownsKeys(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const role = el.getAttribute("role");
  return (
    el.tagName === "BUTTON" ||
    el.tagName === "A" ||
    el.tagName === "SUMMARY" ||
    role === "slider" ||
    role === "tab" ||
    role === "radio" ||
    role === "switch" ||
    role === "checkbox" ||
    role === "button"
  );
}

/**
 * The studio as a rack: a TRANSPORT module on top, then LOOP, MONITOR (narration and inspector) and VERSIONS side by
 * side. Full viewport with no page scroll on desktop; the modules stack when narrow.
 */
export function StudioWorkspace({ mode }: { mode: "replay" | "live" }) {
  const run = useRun((r) => r);
  // Only the status: subscribing to the whole playback clock would re-render the studio on every tick.
  const status = useVerityStore((s) => s.status);
  const { select } = useUi();
  const live = useLive();
  const follow = useFollowFocus(run);

  const isLive = mode === "live";

  // Keyboard shortcuts: only when focus is not in a text field. Escape hands the detail panel
  // back to following the run, from anywhere (the scrubber is an input too).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") {
        select(null);
        return;
      }
      if (isTextField(e.target)) return;
      if (mode !== "replay") return;
      const own = ownsKeys(e.target);
      const p = useVerityStore.getState();
      if (e.key === " " && !own) {
        e.preventDefault();
        if (p.status === "playing") p.pause();
        else p.play();
      } else if (e.key === "ArrowLeft" && !own) {
        const prev = [...p.chapters].reverse().find((c) => c.tMs < p.tMs - 1500);
        p.seek(prev ? prev.tMs : 0);
      } else if (e.key === "ArrowRight" && !own) {
        const next = p.chapters.find((c) => c.tMs > p.tMs + 100);
        p.seek(next ? next.tMs : p.durationMs);
      } else if (e.key === "1") {
        p.setSpeed(1);
      } else if (e.key === "2") {
        p.setSpeed(2);
      } else if (e.key === "Home" && !own) {
        e.preventDefault();
        p.restart();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, select]);

  // Main area by state. Loading and connecting still draw the figure, every step hollow.
  const noData = run.spec === null && run.plan.blocks.length === 0 && run.candidateOrder.length === 0;
  const notice =
    !isLive && status === "loading"
      ? "Loading the recorded run."
      : isLive && noData && (live.connection === "idle" || live.connection === "connecting")
        ? "Connecting to the live run."
        : null;

  let main: ReactNode;
  if (!isLive && status === "error") {
    main = (
      <div className="s-empty">
        <EmptyState
          title="The recorded run could not be read."
          action={
            <button type="button" className="btn sm" onClick={() => window.location.reload()}>
              Try again
            </button>
          }
        />
      </div>
    );
  } else if (isLive && live.connection === "error" && noData) {
    main = (
      <div className="s-empty">
        <EmptyState
          title="This live run is not available anymore."
          action={
            <div className="s-empty-acts">
              <Link prefetch={false} href="/studio?run=mac4-real" className="btn sm">
                Watch the recorded run
              </Link>
              <Link prefetch={false} href="/bench" className="btn ghost sm">
                Try it live
              </Link>
            </div>
          }
        />
      </div>
    );
  } else {
    main = (
      <div className="mid">
        <LoopModule run={run} />
        <Mod label="Monitor" right={run.sample ? <span className="word">Sample data</span> : null}>
          <div className="mon">
            <NarrationFor run={run} mode={mode} notice={notice} />
            <DetailPanel run={run} follow={follow} />
          </div>
        </Mod>
        <CharacteristicsTable run={run} follow={follow} />
      </div>
    );
  }

  return (
    <div className={`${STUDIO_ROOT} studio`}>
      <VignetteGrid />
      <StudioBand mode={mode} />
      <main className="s-main" aria-label="Run">
        <TimelineBar run={run} mode={mode} meta={<RunMeta run={run} mode={mode} />} />
        {main}
      </main>
    </div>
  );
}
