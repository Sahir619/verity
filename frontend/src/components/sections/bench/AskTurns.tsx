"use client";

import { Fragment, type ReactNode } from "react";
import Link from "next/link";
import { familyById } from "@/lib/contracts/presets";
import type { LiveHealth } from "@/lib/contracts/view-types";
import { Sq } from "../studio/loop/LoopModule";
import { AccessCode } from "./AccessCode";
import { formatSmallUsd, knobWords, notReady, proposalChip, withoutCap, type AskTurn } from "./chat";
import { PRIORITIES, blocker, type RunRequest } from "./RequestModule";
import { ReplyDetails } from "./Research";

export type ProposalTurn = Extract<AskTurn, { kind: "proposal" }>;

/** Version names in an answer ("v4") open that version, when the run has it. Everything else stays plain text. */
export function withVersions(text: string, versions: Readonly<Record<string, string>>, onPick: (id: string) => void): ReactNode {
  const parts = text.split(/\b(v\d+)\b/);
  if (parts.length === 1) return text;
  return parts.map((p, i) => {
    const id = i % 2 === 1 ? versions[p] : undefined;
    return id ? (
      <button key={i} type="button" className="b-vlink" onClick={() => onPick(id)} aria-label={`Open ${p}`}>
        {p}
      </button>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    );
  });
}

/**
 * A new run Verity proposes after a question. It looks like the chip card: the settings in plain words and what the last
 * run cost. Nothing runs and nothing is spent until Confirm and run. Only the newest turn has live buttons.
 */
export function ProposalCard({
  turn,
  live,
  health,
  needsCode,
  codeNotice,
  starting,
  startError,
  onConfirm,
  onDismiss,
  onSubmitCode,
}: {
  turn: ProposalTurn;
  live: boolean;
  health: LiveHealth;
  needsCode: boolean;
  codeNotice: string | null;
  starting: boolean;
  startError: string | null;
  onConfirm(turn: ProposalTurn, req: RunRequest): void;
  onDismiss(turnId: string): void;
  onSubmitCode(code: string): Promise<string | null>;
}) {
  const p = turn.proposal;
  const chip = proposalChip(p);
  const family = chip ? familyById(chip.familyId) : undefined;
  if (!chip || !family) {
    return <p className="b-note">I could not use that suggestion: its settings are outside what Verity can build.</p>;
  }
  const goal = PRIORITIES.find((x) => x.id === p.priority)?.label ?? "Smaller";
  const title = family.describe(chip.knobs);
  const rows = [{ label: "Chip", value: family.name }, ...knobWords(chip), { label: "Goal", value: goal }, ...(p.idea ? [{ label: "Idea", value: p.idea }] : [])];
  const cost = p.lastRunUsd !== null ? `The last run cost ${formatSmallUsd(p.lastRunUsd)}.` : "";

  if (turn.state !== "open" || !live) {
    return (
      <div className="b-card" data-quiet="">
        <p className="b-card-t">{title}</p>
        <p className="b-card-s">
          {turn.state === "started" ? "Started as a new run. " : turn.state === "dismissed" ? "Not now. " : "Not run. "}
          {turn.state === "started" && turn.newRunId ? (
            <Link prefetch={false} href={`/bench?run=${encodeURIComponent(turn.newRunId)}`} className="b-link">
              Open that run
            </Link>
          ) : null}
        </p>
      </div>
    );
  }

  const ready = health.status === "ready";
  const why = blocker(family, chip.knobs, health);
  const canRun = ready && !starting && !needsCode && why === null;
  return (
    <form
      className="b-card"
      data-busy={starting ? "" : undefined}
      aria-label={`Proposed run: ${title}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (canRun) onConfirm(turn, { familyId: family.id, knobs: chip.knobs, priority: p.priority, title, sendKnobs: health.features?.includes("params") === true });
      }}
    >
      <p className="b-card-t">{title}</p>
      <dl className="b-rows">
        {rows.map((r) => (
          <div key={r.label}>
            <dt>{r.label}</dt>
            <dd>{r.value}</dd>
          </div>
        ))}
      </dl>
      {cost ? <p className="b-card-s">{cost}</p> : null}
      <div className="b-acts">
        <button type="submit" className="bx pri" disabled={!canRun}>
          {starting ? "Starting..." : "Confirm and run"}
        </button>
        <button type="button" className="bx ghost" disabled={starting} onClick={() => onDismiss(turn.id)}>
          Not now
        </button>
      </div>
      {health.status === "checking" ? (
        <p className="b-note">Checking whether live runs are open.</p>
      ) : !ready ? (
        <p className="b-note">{notReady(health)}</p>
      ) : needsCode ? (
        <AccessCode onSubmit={onSubmitCode} notice={codeNotice} />
      ) : why ? (
        <p className="b-note">{why}</p>
      ) : null}
      {startError && ready ? (
        <p className="b-note" role="alert">
          <span className="rkq fail" aria-hidden /> {startError}
        </p>
      ) : null}
    </form>
  );
}

/** "Show the walkthrough, 6 steps" and the steps in a dropdown. The walkthrough itself shows on the Architecture tab; moving through it never calls the model. */
export function Walkin({ steps, showing, onShow }: { steps: { title: string }[]; showing: boolean; onShow(): void }) {
  return (
    <div className="b-walkin">
      <button type="button" className="bx ghost sm" aria-pressed={showing} onClick={onShow}>
        {showing ? "Showing the walkthrough" : `Show the walkthrough, ${steps.length} steps`}
      </button>
      <details className="b-more">
        <summary>The steps</summary>
        <ol className="b-walk-l">
          {steps.map((st, j) => (
            <li key={j}>{st.title}</li>
          ))}
        </ol>
      </details>
    </div>
  );
}

/**
 * The conversation under a finished run: the reader's questions and Verity's replies. A reply is an answer from the run's
 * own data, a proposed new run, a pointer to a new chip, or a plain refusal. Only the newest turn has live buttons.
 */
export function AskTurns({
  turns,
  health,
  needsCode,
  codeNotice,
  starting,
  startError,
  onConfirm,
  onDismiss,
  onNewChip,
  onCode,
  onSubmitCode,
  versions,
  onPickVersion,
  walkTurnId,
  onWalk,
}: {
  turns: AskTurn[];
  health: LiveHealth;
  needsCode: boolean;
  codeNotice: string | null;
  starting: boolean;
  startError: string | null;
  onConfirm(turn: ProposalTurn, req: RunRequest): void;
  onDismiss(turnId: string): void;
  /** Opens a new thread and fact-checks this sentence there. */
  onNewChip(prompt: string): void;
  /** The access code from an error that needed one; the question is asked again once it is accepted. */
  onCode(turnId: string, prompt: string, code: string): Promise<string | null>;
  onSubmitCode(code: string): Promise<string | null>;
  /** The run's versions by name ("v4" to its id), so a name in an answer can open it. */
  versions: Readonly<Record<string, string>>;
  onPickVersion(id: string): void;
  /** The answer whose walkthrough is showing, if any. */
  walkTurnId: string | null;
  /** Shows an answer's walkthrough on the Architecture tab. No new model call. */
  onWalk(turnId: string): void;
}) {
  const last = turns.length - 1;
  return (
    <>
      {turns.map((t, i) => {
        const live = i === last;
        switch (t.kind) {
          case "user":
            return (
              <div key={t.id} className="b-me">
                {t.version ? (
                  <span className="b-me-v m" aria-label={`About ${t.version}`}>
                    {t.version}
                  </span>
                ) : null}
                {t.text}
              </div>
            );
          case "asking":
            return (
              <p key={t.id} className="b-ai b-wait" role="status">
                <Sq kind="run" /> Looking through this run...
              </p>
            );
          case "answer":
            return (
              <div key={t.id} className="b-ai">
                <p>{withVersions(withoutCap(t.text), versions, onPickVersion)}</p>
                {t.bullets.length > 0 ? (
                  <ul className="b-bul">
                    {t.bullets.map((b, j) => (
                      <li key={j}>{withVersions(b, versions, onPickVersion)}</li>
                    ))}
                  </ul>
                ) : null}
                {t.steps ? <Walkin steps={t.steps} showing={walkTurnId === t.id} onShow={() => onWalk(t.id)} /> : null}
                {t.basis ? (
                  <ReplyDetails>
                    <p className="b-note b-dim">{t.basis}</p>
                  </ReplyDetails>
                ) : null}
              </div>
            );
          case "proposal":
            return (
              <div key={t.id} className="b-ai">
                <p>{withoutCap(t.text)}</p>
                <ProposalCard
                  turn={t}
                  live={live}
                  health={health}
                  needsCode={needsCode}
                  codeNotice={codeNotice}
                  starting={starting}
                  startError={live ? startError : null}
                  onConfirm={onConfirm}
                  onDismiss={onDismiss}
                  onSubmitCode={onSubmitCode}
                />
              </div>
            );
          case "new_chip":
            return (
              <div key={t.id} className="b-ai">
                <p>{t.text}</p>
                {live ? (
                  <div className="b-acts">
                    <button type="button" className="bx ghost" onClick={() => onNewChip(t.prompt)}>
                      Start a new chip
                    </button>
                  </div>
                ) : null}
              </div>
            );
          case "refused":
            return (
              <div key={t.id} className="b-ai">
                <p>{t.text}</p>
              </div>
            );
          case "error":
            return (
              <div key={t.id} className="b-ai">
                {live && t.needsCode ? <AccessCode onSubmit={(code) => onCode(t.id, t.prompt, code)} notice={t.text} /> : <p role="alert">{t.text}</p>}
              </div>
            );
        }
      })}
    </>
  );
}
