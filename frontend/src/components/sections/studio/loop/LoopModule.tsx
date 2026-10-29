"use client";

import { useMemo, type CSSProperties } from "react";
import type { RunState } from "@/lib/contracts/types";
import { Mod } from "../chrome/Mod";
import { deriveLoop, SEGMENTS, type LedState } from "./derive-leds";

/** The status square: filled amber = pass, hollow white = fail or open, pale = running, grey = waiting. */
export function Sq({ kind }: { kind: "pass" | "fail" | "run" | "wait" | "drop" | "done" }) {
  return <span className={`rkq ${kind}`} aria-hidden />;
}

function sqFor(state: LedState): "pass" | "fail" | "run" | "wait" {
  if (state === "done") return "pass";
  if (state === "run") return "run";
  if (state === "open" || state === "halt") return "fail";
  return "wait";
}

/**
 * The LOOP module: six LED columns that light as the run progresses, hollow fail marks beside TEST, and the round
 * counter. Everything is derived from the run record; nothing is timed or hardcoded.
 */
export function LoopModule({ run }: { run: RunState }) {
  const m = useMemo(() => deriveLoop(run), [run]);
  const { round } = m;
  const roundText = round.total !== null ? `${round.n}/${round.total}` : String(round.n);

  return (
    <Mod label="Loop" right={<span className="word">{m.word}</span>}>
      <div className="lm-body">
        <ol className="leds" aria-label="Loop stages">
          {m.cols.map((c) => (
            <li key={c.key} className="col" data-state={c.state}>
              <div className="segs" aria-hidden>
                {Array.from({ length: SEGMENTS }, (_, j) => (
                  <span key={j} className={`seg${j < c.segs ? (c.state === "run" || c.state === "halt" ? " run" : c.state === "open" ? " plain" : " on") : ""}`} />
                ))}
                {c.key === "test"
                  ? m.fails.map((f, k) => (
                      <i key={f.key} className="fmark" style={{ "--row": Math.min(SEGMENTS - 1, 2 + k * 2) } as CSSProperties} />
                    ))
                  : null}
              </div>
              <div className="cn">{c.label}</div>
              <div className="cs">
                <Sq kind={sqFor(c.state)} />
                <span>{c.word}</span>
              </div>
              <span className="sr">
                {c.label}: {c.word}
              </span>
            </li>
          ))}
        </ol>

        <div className="fixes">
          <span className="lab">
            {m.fails.length} {m.fails.length === 1 ? "failed attempt" : "failed attempts"}
          </span>
          {m.fails.map((f) => (
            <span key={f.key} className="fx">
              <span className="rkq fail" aria-hidden />
              {f.tag} {f.text}
            </span>
          ))}
        </div>

        <div className="round">
          <svg viewBox="0 0 64 64" aria-hidden focusable="false">
            <circle cx="32" cy="32" r="29" className="rd-bg" strokeWidth="1.5" />
            <text x="32" y="30" textAnchor="middle" className="rd-l">
              ROUND
            </text>
            <text x="32" y="43" textAnchor="middle" className="rd-n">
              {roundText}
            </text>
          </svg>
          <p>
            <span className="lab rd-h">{round.total !== null ? `Round ${round.n} of ${round.total}` : `Round ${round.n}`}</span>
            {round.text}
          </p>
        </div>
      </div>
    </Mod>
  );
}
