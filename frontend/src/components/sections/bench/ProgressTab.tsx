"use client";

import Link from "next/link";
import type { Priority, RunState } from "@/lib/contracts/types";
import type { LiveConnection } from "@/lib/contracts/view-types";
import { Sq } from "../studio/loop/LoopModule";
import { creditsLine, plainCaption, progressOf, runSteps } from "./chat";
import { TERMINAL } from "./derive";

const STATE: Record<RunState["phase"], { kind: "pass" | "fail" | "run" | "wait" | "drop"; word: string }> = {
  idle: { kind: "wait", word: "Connecting" },
  starting: { kind: "run", word: "Starting" },
  running: { kind: "run", word: "Running" },
  done: { kind: "pass", word: "Done" },
  error: { kind: "fail", word: "Failed" },
  cap_reached: { kind: "fail", word: "Spend limit reached" },
  stopped: { kind: "drop", word: "Stopped" },
};

/**
 * The Progress tab of the right panel: five plain steps that tick off as the run moves, the agent's latest line, the
 * credits and Stop. The tool names live in "What each step means". It stays after the run ends and shows the final steps.
 */
export function ProgressTab({
  run,
  priority,
  connection,
  liveError,
  stopping,
  onStop,
  onReconnect,
}: {
  run: RunState;
  priority: Priority;
  connection: LiveConnection;
  liveError: string | null;
  stopping: boolean;
  onStop(): void;
  onReconnect(): void;
}) {
  const ended = TERMINAL.has(run.phase);
  const steps = runSteps(run, priority);
  const now = steps.find((s) => s.kind === "run");
  const lost = !ended && connection === "error";
  // Nothing has arrived from this run yet: no claim that it is still going, and nothing to stop.
  const unheard = run.phase === "idle";
  const st = STATE[run.phase];
  const caption = lost
    ? null
    : run.caption
      ? plainCaption(run.caption.text)
      : connection === "connecting" || connection === "retrying"
        ? "Connecting to the run."
        : unheard
          ? "Waiting for the run to start."
          : null;

  return (
    <section className="b-prog" aria-label="Run progress" data-ended={ended ? "" : undefined}>
      <div className="b-prog-h">
        <span className="b-state">
          <Sq kind={st.kind} /> {lost ? "Connection lost" : st.word}
        </span>
      </div>
      <div className="b-bar" aria-hidden>
        <i style={{ transform: `scaleX(${progressOf(steps)})` }} />
      </div>
      <ol className="b-steps">
        {steps.map((s) => (
          <li key={s.id} data-kind={s.kind}>
            <Sq kind={s.kind} />
            <span>{s.label}</span>
          </li>
        ))}
      </ol>
      <div className="b-live" aria-live="polite">
        {now && !ended ? <p className="sr">{now.label}</p> : null}
        {!ended && caption ? <p className="b-cap">{caption}</p> : null}
      </div>
      {lost ? (
        <div className="b-lost" role="alert">
          <p>{unheard ? "Could not open this run." : "Lost the connection to this run. It keeps going on the server."}</p>
          <div className="b-acts">
            <button type="button" className="bx ghost sm" onClick={onReconnect}>
              Reconnect
            </button>
            {unheard ? (
              <Link prefetch={false} href="/studio?run=mac4-real" className="bx ghost sm">
                Watch a recorded run
              </Link>
            ) : null}
          </div>
        </div>
      ) : null}
      {liveError && !lost && (!ended || run.phase === "error") ? (
        <p className="b-note" role="alert">
          {liveError}
        </p>
      ) : null}
      <div className="b-prog-f">
        <span className="b-credits">{unheard ? "" : creditsLine(run)}</span>
        {!ended && !unheard ? (
          <button type="button" className="bx ghost sm" disabled={stopping} onClick={onStop}>
            {stopping ? "Stopping..." : "Stop"}
          </button>
        ) : null}
      </div>
      <div className="b-explain">
        <details className="b-more">
          <summary>What each step means</summary>
          <dl className="b-more-dl">
            <dt>Plan</dt>
            <dd>The agent, an AI model, splits your chip into a few blocks.</dd>
            <dt>Answer key</dt>
            <dd>
              A golden model in Python and a set of tests are written, then locked away from the agent. It cannot change what counts as
              correct.
            </dd>
            <dt>First version</dt>
            <dd>
              The agent writes Verilog (v1). Verilator checks the code, then iverilog and cocotb run the locked tests. When a test fails, the
              agent reads the failure and fixes the code.
            </dd>
            <dt>Smaller or faster versions</dt>
            <dd>
              New versions compete in small rounds. Each must pass the same tests, then Yosys and OpenSTA estimate its area, clock period and
              power after synthesis. This is a post-synthesis estimate with no wires, not a layout.
            </dd>
            <dt>Proof</dt>
            <dd>Yosys EQY checks that the winner gives the same answers as v1 for every input, not only the tested ones.</dd>
          </dl>
        </details>
        <details className="b-more">
          <summary>What do the credits mean</summary>
          <p>
            Credits are what this run has spent on AI model calls, in US dollars.
 The server stops a run that reaches its spend limit. One live run goes at a
            time.
          </p>
        </details>
      </div>
    </section>
  );
}
