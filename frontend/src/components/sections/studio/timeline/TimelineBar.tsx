"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Download, RotateCcw, SkipForward } from "lucide-react";
import type { Chapter, RunState } from "@/lib/contracts/types";
import { formatClock, formatDelta, formatUsd } from "@/lib/format";
import { priorityMetric } from "@/lib/run/fitness";
import { useLive, usePlayback, useVerityStore } from "@/lib/store";
import { useRunZip } from "@/components/sections/shared/use-run-zip";
import { Mod } from "../chrome/Mod";
import { partNumber } from "../chrome/StudioBand";
import { Seg7 } from "../chrome/Seg7";
import { proofText } from "../figure/proof";
import { Narration, type NarrationStatus } from "./Narration";

const FINISHED = new Set(["done", "error", "cap_reached", "stopped"]);

function chapterIndex(chapters: Chapter[], tMs: number): number {
  let found = -1;
  chapters.forEach((c, i) => {
    if (c.tMs <= tMs) found = i;
  });
  return found;
}

/** "Winner v5: area −17.4% vs v1. PROVEN EQUAL (EQY)." Built only from the record. */
function winnerLine(run: RunState): string {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  if (!winner) return "No version passed the locked tests.";
  const lead = run.phase === "done" ? `Winner ${winner.label}` : `Best so far ${winner.label}`;
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const parts: string[] = [];
  const metric = priorityMetric(run.spec?.priority ?? "area");
  const key = metric.key === "area" ? "areaUm2" : metric.key === "clockPeriod" ? "clockPeriodNs" : "powerMw";
  const value = winner.ppa?.[key] ?? null;
  const baseValue = base?.ppa?.[key] ?? null;
  if (value !== null && baseValue !== null && base && base.id !== winner.id) {
    parts.push(`${metric.label.toLowerCase()} ${formatDelta(value, baseValue, { lowerIsBetter: true }).text} vs ${base.label}`);
  }
  if (winner.eqy) parts.push(proofText(winner.eqy, base?.label ?? "v1").label);
  return parts.length > 0 ? `${lead}: ${parts.join(". ")}.` : `${lead}.`;
}

function Scrubber({
  tMs,
  durationMs,
  chapters,
  disabled,
  onSeek,
}: {
  tMs: number;
  durationMs: number;
  chapters: Chapter[];
  disabled: boolean;
  onSeek(t: number): void;
}) {
  const max = Math.max(1, durationMs);
  const value = Math.min(Math.max(tMs, 0), max);
  const ci = chapterIndex(chapters, value);
  const chapter = ci >= 0 ? chapters[ci] : null;
  const clock = `${formatClock(value)} of ${formatClock(durationMs)}`;
  const pct = (value / max) * 100;
  return (
    <div className="tlb-scrub">
      <div className="tlb-scrub-in">
        <input
          type="range"
          min={0}
          max={max}
          step={500}
          value={value}
          disabled={disabled}
          aria-label="Replay position"
          aria-valuetext={chapter ? `${clock}, ${chapter.label}` : clock}
          onChange={(e) => onSeek(Number(e.target.value))}
          style={{ "--p": `${pct}%` } as CSSProperties}
        />
        <ol className="tlb-ch" aria-label="Chapters">
          {chapters.map((c, i) => {
            const at = (c.tMs / max) * 100;
            return (
              <li key={c.id} style={{ left: `${at}%` }} data-row={i % 2} data-edge={at > 90 ? "end" : undefined} data-on={i === ci ? "" : undefined}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onSeek(c.tMs)}
                  aria-label={`Jump to ${c.label}, ${formatClock(c.tMs)}`}
                  aria-current={i === ci ? "true" : undefined}
                >
                  {i + 1} {c.label}
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

/** Next steps when the run has ended. The winner line itself sits in the MONITOR module. */
function EndActions({ run, resultsHref, onReplay }: { run: RunState; resultsHref: string; onReplay?: () => void }) {
  const zip = useRunZip();
  const hasWinner = Boolean(run.winnerId);
  return (
    <div className="tlb-acts">
      {onReplay ? (
        <button type="button" className="btn ghost sm" onClick={onReplay}>
          <RotateCcw aria-hidden className="ico" />
          Watch again
        </button>
      ) : null}
      {hasWinner ? (
        <button type="button" className="btn ghost sm" onClick={() => void zip.download()} disabled={zip.state === "busy"}>
          <Download aria-hidden className="ico" />
          {zip.state === "busy" ? "Preparing" : zip.state === "error" ? "Download failed, retry" : "Download zip"}
        </button>
      ) : null}
      <Link prefetch={false} href={resultsHref} className="btn sm">
        See results
      </Link>
    </div>
  );
}

/**
 * The caption of the MONITOR module: the current narration, large. A status message (lost connection, stopped, cap)
 * replaces the caption when one applies; when the run ends it carries the winner line.
 */
export function NarrationFor({ run, mode, notice }: { run: RunState; mode: "replay" | "live"; notice: string | null }) {
  const live = useLive();
  const isLive = mode === "live";
  const finished = FINISHED.has(run.phase);

  const status: NarrationStatus | null = (() => {
    const recorded = (
      <Link prefetch={false} href="/studio?run=mac4-real" className="btn ghost xs">
        Watch the recorded run
      </Link>
    );
    const another = (
      <Link prefetch={false} href="/bench" className="btn ghost xs">
        Start another
      </Link>
    );
    if (isLive && live.connection === "error" && (run.phase === "starting" || run.phase === "running" || run.phase === "idle")) {
      return {
        tone: "fail",
        word: "Lost",
        text: "Lost connection to the live run. The run may still finish on the server.",
        actions: (
          <>
            <button type="button" className="btn ghost xs" onClick={() => window.location.reload()}>
              Reconnect
            </button>
            {recorded}
          </>
        ),
      };
    }
    if (run.phase === "error") {
      return { tone: "fail", word: "Error", text: `The run stopped: ${live.error ?? "the agent reported an error."} Everything finished so far is kept.`, actions: another };
    }
    if (run.phase === "cap_reached") {
      return { tone: "warn", word: "Limit", text: `The run reached its spend limit. The agent stopped after round ${run.round} and kept the best version so far.` };
    }
    if (run.phase === "stopped") return { tone: "plain", word: "Stopped", text: "You stopped the run. Everything finished so far is kept.", actions: another };
    return null;
  })();

  return (
    <Narration
      text={notice ?? (finished ? winnerLine(run) : (run.caption?.text ?? null))}
      chapter={notice ? undefined : finished ? "Result" : run.caption?.chapter}
      at={notice || finished ? null : (run.caption?.tMs ?? null)}
      now={run.tMs}
      strong={finished && !notice}
      status={status}
    />
  );
}

/**
 * The TRANSPORT module. Replay: run id and title, play, pause and restart, speed, a seven-segment time and chapter
 * readout, and a scrubber with one tick per chapter. Live: connection, elapsed time, round, credits and Stop. When the
 * run ends it carries the next steps.
 */
export function TimelineBar({ run, mode, meta }: { run: RunState; mode: "replay" | "live"; meta: ReactNode }) {
  const router = useRouter();
  const playback = usePlayback();
  const live = useLive();
  const [stopConfirm, setStopConfirm] = useState(false);
  const [stopping, setStopping] = useState(false);

  const isLive = mode === "live";
  const finished = FINISHED.has(run.phase);
  const resultsHref = `/results?run=${encodeURIComponent(isLive ? run.runId : run.sample ? "golden" : "mac4-real")}`;
  // zod strips unknown meta fields, so the compression is read from meta.note (written by scripts/compress-fixture.ts).
  const note = useVerityStore((s) => s.logMeta?.note) ?? "";
  const shortened = /idle waits shortened/i.test(note);
  const originalClock = /Original duration (\d+:\d{2})/.exec(note)?.[1];
  const recordedLabel = run.sample
    ? `Recorded run · ${formatClock(playback.durationMs)}`
    : shortened && originalClock
      ? `Real run · ${originalClock} recorded, idle waits shortened`
      : `Real run · ${formatClock(playback.durationMs)}`;
  const unavailable = playback.status === "loading" || playback.status === "error";
  const playing = playback.status === "playing";
  const ended = playback.status === "ended";

  const part = partNumber(run);
  const title = run.spec?.title ?? run.title;

  const confirmStop = () => {
    setStopConfirm(false);
    setStopping(true);
    void live.stopRun().finally(() => setStopping(false));
  };

  const runid = (
    <div className="runid">
      <span className="m">{run.runId}</span>
      <h1>
        {part ? `${part} · ` : ""}
        {title}
      </h1>
      <span className="lab">{isLive ? "Live run" : recordedLabel}</span>
    </div>
  );

  let body: ReactNode;
  if (isLive) {
    const conn = live.connection === "idle" ? "connecting" : live.connection;
    body = (
      <div className="tr" data-live="">
        {runid}
        <div className="disp">
          <div className="grp">
            <span className="lab">Status</span>
            <span className={`mk ${conn === "open" ? "win" : conn === "error" ? "fail" : "warn"}`}>
              {conn === "open"
                ? "Live"
                : conn === "error"
                  ? "Live, disconnected"
                  : conn === "retrying"
                    ? "Live, reconnecting"
                    : conn === "closed"
                      ? "Live, closed"
                      : "Live, connecting"}
            </span>
          </div>
          <div className="grp">
            <span className="lab">Time</span>
            <Seg7 text={formatClock(run.tMs).padStart(5, "0")} label={`Elapsed ${formatClock(run.tMs)}`} />
          </div>
          <div className="grp">
            <span className="lab">Round</span>
            <span className="m">{run.round > 0 ? `Round ${run.round}` : "Building"}</span>
          </div>
          <div className="grp">
            <span className="lab">Credits</span>
            <span className="tlb-credits">
              <span className="m">{`${formatUsd(run.credits.usedUsd)} spent`}</span>
            </span>
          </div>
        </div>
        <div className="acts">
          {finished ? (
            <EndActions run={run} resultsHref={resultsHref} />
          ) : run.phase === "running" || run.phase === "starting" ? (
            stopConfirm ? (
              <div className="tlb-acts" role="group" aria-label="Confirm stopping the run">
                <span className="tlb-q">Stop this run? You keep everything finished so far.</span>
                <button type="button" className="btn sm" onClick={confirmStop}>
                  Stop
                </button>
                <button type="button" className="btn ghost sm" onClick={() => setStopConfirm(false)}>
                  Keep running
                </button>
              </div>
            ) : (
              <button type="button" className="btn ghost sm" onClick={() => setStopConfirm(true)} disabled={stopping}>
                {stopping ? "Stopping" : "Stop run"}
              </button>
            )
          ) : null}
        </div>
      </div>
    );
  } else {
    const ci = chapterIndex(playback.chapters, playback.tMs);
    const chapter = ci >= 0 ? playback.chapters[ci] : null;
    const clockText = formatClock(playback.tMs).padStart(5, "0");
    const nCh = playback.chapters.length;
    body = (
      <div className="tr">
        {runid}
        <div className="ctl" role="group" aria-label="Playback">
          <button type="button" className="ib" disabled={unavailable} aria-label={ended ? "Watch again" : "Play"} aria-pressed={playing} onClick={ended ? playback.restart : playback.play}>
            <svg viewBox="0 0 18 18" aria-hidden>
              <path d="M5 3v12l10-6z" />
            </svg>
          </button>
          <button type="button" className="ib" disabled={unavailable || ended} aria-label="Pause" aria-pressed={!playing && !ended} onClick={playback.pause}>
            <svg viewBox="0 0 18 18" aria-hidden>
              <path d="M5 3v12M13 3v12" strokeWidth="3" strokeLinecap="butt" />
            </svg>
          </button>
          <button type="button" className="ib" disabled={unavailable} aria-label="Restart from the start" onClick={playback.restart}>
            <svg viewBox="0 0 18 18" aria-hidden>
              <path d="M4 3v12M15 3L7 9l8 6z" />
            </svg>
          </button>
        </div>
        <div className="spdw">
          <div className="lab">Speed</div>
          <div className="spd" role="group" aria-label="Playback speed">
            {([1, 2] as const).map((s) => (
              <button key={s} type="button" aria-pressed={playback.speed === s} disabled={unavailable} onClick={() => playback.setSpeed(s)}>
                {s}x
              </button>
            ))}
          </div>
        </div>
        <div className="disp">
          <div className="grp">
            <span className="lab">Time</span>
            <Seg7 text={clockText} label={`${formatClock(playback.tMs)} of ${formatClock(playback.durationMs)}`} />
          </div>
          <div className="grp">
            <span className="lab">Chapter</span>
            <Seg7 text={String(Math.max(ci + 1, 0)).padStart(2, "0")} label={chapter ? `Chapter ${ci + 1} of ${nCh}` : "No chapter yet"} />
          </div>
          <div className="dn">{chapter ? `${chapter.label} (${ci + 1} of ${nCh})` : "Waiting"}</div>
        </div>
        <div className="acts">
          {finished ? (
            <EndActions run={run} resultsHref={resultsHref} onReplay={playback.restart} />
          ) : (
            <button
              type="button"
              className="btn ghost sm"
              disabled={unavailable}
              onClick={() => {
                playback.seek(playback.durationMs);
                router.push(resultsHref);
              }}
            >
              <SkipForward aria-hidden className="ico" />
              Skip to results
            </button>
          )}
        </div>
        {playback.status === "error" ? (
          <p role="alert" className="tlb-err">
            The recording could not be loaded.
          </p>
        ) : (
          <Scrubber tMs={playback.tMs} durationMs={playback.durationMs} chapters={playback.chapters} disabled={unavailable} onSeek={playback.seek} />
        )}
      </div>
    );
  }

  return (
    <Mod label="Transport" right={meta} className="transport" ariaLabel={isLive ? "Live run controls" : "Replay controls"}>
      {body}
    </Mod>
  );
}
