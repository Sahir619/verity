"use client";

import Link from "next/link";
import type { ToolStep } from "@/lib/contracts/agent";
import { Walkin, withVersions } from "./AskTurns";
import { withoutCap, type ChatMessage } from "./chat";
import { ReplyDetails, ResearchBlock } from "./Research";

type AnswerMessage = Extract<ChatMessage, { kind: "answer" }>;

/**
 * What the chip engineer agent did before it replied: one dim line, "Used 3 tools", closed. Opened, it lists one line per
 * tool that code ran, a check mark when it worked and a plain mark when it did not. A tool that failed is counted on the
 * closed line too. The labels are the server's own words.
 */
export function ToolSteps({ steps }: { steps: ToolStep[] }) {
  const failed = steps.filter((s) => !s.ok).length;
  const used = steps.length === 1 ? "Used 1 tool" : `Used ${steps.length} tools`;
  return (
    <details className="b-more b-toold">
      <summary>{failed > 0 ? `${used}, ${failed} did not work` : used}</summary>
      <ul className="b-tools" aria-label="What Verity did">
        {steps.map((s, i) => (
          <li key={i}>
            <span className="b-tk" aria-hidden>
              {s.ok ? "✓" : "×"}
            </span>
            {s.ok ? null : <span className="sr">Did not work: </span>}
            {s.label}
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * An agent answer: the tools it used (one closed line), its words, bullets, suggestion chips (each one sends itself as the
 * next message), and a walkthrough of the run when it made one. The research behind it with its sources, and how the words
 * were checked, sit in one closed Details. Chips show on the newest answer only. The numbers in the words were checked by
 * the server's code before they got here.
 */
export function AgentAnswer({
  m,
  live,
  chipsOff,
  versions,
  onPickVersion,
  canWalk,
  walking,
  onWalk,
  onChip,
}: {
  m: AnswerMessage;
  /** The newest message: its chips can be tapped. */
  live: boolean;
  /** The chat box is waiting on a run, so a chip would not send. */
  chipsOff: boolean;
  /** The run's versions by name ("v4" to its id), so a name in an answer can open it. */
  versions: Readonly<Record<string, string>>;
  onPickVersion(id: string): void;
  /** The walkthrough is about the run on screen, so it can show on the Architecture tab. */
  canWalk: boolean;
  /** This answer's walkthrough is the one showing. */
  walking: boolean;
  onWalk(): void;
  onChip(text: string): void;
}) {
  return (
    <div className="b-ai">
      {m.toolSteps && m.toolSteps.length > 0 ? <ToolSteps steps={m.toolSteps} /> : null}
      <p>{withVersions(withoutCap(m.text), versions, onPickVersion)}</p>
      {m.bullets.length > 0 ? (
        <ul className="b-bul">
          {m.bullets.map((b, j) => (
            <li key={j}>{withVersions(b, versions, onPickVersion)}</li>
          ))}
        </ul>
      ) : null}
      {live && m.chips && m.chips.length > 0 ? (
        <div className="b-acts" role="group" aria-label="Suggestions">
          {m.chips.map((c) => (
            <button key={c} type="button" className="b-chip" disabled={chipsOff} onClick={() => onChip(c)}>
              {c}
            </button>
          ))}
        </div>
      ) : null}
      {m.steps ? (
        canWalk ? (
          <Walkin steps={m.steps} showing={walking} onShow={onWalk} />
        ) : (
          <details className="b-more">
            <summary>The steps</summary>
            <ol className="b-walk-l">
              {m.steps.map((st, j) => (
                <li key={j}>{st.title}</li>
              ))}
            </ol>
          </details>
        )
      ) : null}
      {m.research || m.basis ? (
        <ReplyDetails>
          {m.research ? <ResearchBlock research={m.research} /> : null}
          {m.basis ? <p className="b-note b-dim">{m.basis}</p> : null}
        </ReplyDetails>
      ) : null}
    </div>
  );
}

/** A run of this thread that is not the one on screen: a quiet record with a way back to it. */
export function RunNote({ runId, title }: { runId: string; title: string | null }) {
  return (
    <div className="b-card" data-quiet="">
      <p className="b-card-t">{title ?? "A run"}</p>
      <p className="b-card-s">
        Started as a run.{" "}
        <Link prefetch={false} href={`/bench?run=${encodeURIComponent(runId)}`} className="b-link">
          Open that run
        </Link>
      </p>
    </div>
  );
}
