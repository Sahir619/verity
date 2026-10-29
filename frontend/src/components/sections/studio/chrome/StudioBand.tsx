"use client";

import { useMemo } from "react";
import { RackBar, RackBarLink } from "@/components/sections/shared/RackBar";
import type { RunState } from "@/lib/contracts/types";
import { formatUsd } from "@/lib/format";
import { selectRunSummary } from "@/lib/run/selectors";
import { useLive } from "@/lib/store";

/** "VRT-MAC4" from the run's preset id, the same part-number style as the landing. */
export function partNumber(run: RunState): string | null {
  const id = run.spec?.presetId;
  return id ? `VRT-${id.toUpperCase()}` : null;
}

/** The top band: the shared rack bar, with the way to a live run. The run itself is named in the TRANSPORT module. */
export function StudioBand({ mode }: { mode: "replay" | "live" }) {
  return (
    <RackBar
      current="studio"
      actions={mode === "live" ? undefined : <RackBarLink href="/bench">Try it live</RackBarLink>}
    />
  );
}

/** Sample stamp, mode, human inputs and cost: the right side of the TRANSPORT module header. */
export function RunMeta({ run, mode }: { run: RunState; mode: "replay" | "live" }) {
  const summary = useMemo(() => selectRunSummary(run), [run]);
  const { connection } = useLive();
  const isLive = mode === "live";
  const conn = connection === "idle" ? "connecting" : connection;

  const modeMk = isLive
    ? conn === "open"
      ? { tone: "win", word: "Live" }
      : conn === "error"
        ? { tone: "fail", word: "Live, disconnected" }
        : { tone: "warn", word: conn === "retrying" ? "Live, reconnecting" : conn === "closed" ? "Live, closed" : "Live, connecting" }
    : { tone: "plain", word: "Replay" };

  const cost = isLive
    ? `${formatUsd(run.credits.usedUsd)} spent`
    : `${formatUsd(0)} replay`;
  const items = summary.humanInputItems;

  return (
    <div className="smeta" role="group" aria-label="Run information">
      {run.sample ? <span className="badge">Sample data</span> : null}
      <span className={`mk ${modeMk.tone}`}>{modeMk.word}</span>
      <details className="smeta-pop">
        <summary>
          Human inputs <b className="mono">{items.length}</b>
        </summary>
        {items.length > 0 ? (
          <ol>
            {items.map((it, i) => (
              <li key={i}>
                {it.label}
                {it.local ? " (this replay only)" : ""}
              </li>
            ))}
          </ol>
        ) : (
          <p>None yet. The run is on Autopilot.</p>
        )}
      </details>
      <span className="smeta-cost" title={summary.recordedUsd !== null ? `The recording reports ${formatUsd(summary.recordedUsd)}.` : undefined}>
        <b className="mono">{cost}</b>
      </span>
    </div>
  );
}
