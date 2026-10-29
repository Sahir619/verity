"use client";

import { Fragment, useEffect, useRef, type ReactNode, type RefObject } from "react";
import Link from "next/link";
import { familyById } from "@/lib/contracts/presets";
import type { Priority, RunState } from "@/lib/contracts/types";
import type { LiveConnection, LiveHealth } from "@/lib/contracts/view-types";
import { formatUsd } from "@/lib/format";
import { useReducedMotion } from "@/lib/hooks/use-reduced-motion";
import { Sq } from "../studio/loop/LoopModule";
import { AccessCode } from "./AccessCode";
import { AgentAnswer, RunNote, ToolSteps } from "./AgentTurn";
import { ProposalCard, type ProposalTurn } from "./AskTurns";
import { CannotBuild, ChipCard } from "./ChipCard";
import { CpuCard } from "./CpuCard";
import { CpuProposalCard } from "./CpuProposalCard";
import { assumedWords, doneSentence, unchecked, withoutCap, type ChatMessage, type Chip } from "./chat";
import { EXAMPLES } from "./ExamplePrompts";
import type { RunRequest } from "./RequestModule";
import { ReplyDetails, ResearchBlock, VerdictLine } from "./Research";

/** A family name inside a sentence, with its article: "an adder", "a MAC array" (acronyms keep their case). */
function inSentence(name: string): string {
  const word = name.length > 1 && name[1] === name[1].toLowerCase() ? name[0].toLowerCase() + name.slice(1) : name;
  return `${/^[aeiou]/i.test(word) ? "an" : "a"} ${word}`;
}

/**
 * The messages of the thread: what the user typed and what Verity answered. Only the newest answer has live buttons.
 * Without the agent that is the messages before a run, and the run's lines follow them. With the agent (`threaded`) the
 * thread is the whole conversation: replies come after the run too, and the run's lines show at the marker of the run
 * in the URL. A card or proposal stays live until a later card, proposal or run replaces it.
 */
export function ChatThread({
  messages,
  health,
  needsCode,
  codeNotice,
  starting,
  startError,
  runStarted,
  threaded,
  currentRun,
  runTitle,
  canRetry,
  versions,
  onPickVersion,
  walkTurnId,
  onWalk,
  chipsOff,
  onChip,
  onConfirm,
  onDismiss,
  onRun,
  onPickYourself,
  onClosest,
  onExample,
  onRetry,
  onCode,
  onSubmitCode,
  scrollKey,
  cpuCanBuild,
  threadId,
  onCpuStarted,
  onCpuProposalStarted,
  onCpuProposalDismiss,
  onShowBoard,
  children,
}: {
  messages: ChatMessage[];
  health: LiveHealth;
  needsCode: boolean;
  codeNotice: string | null;
  starting: boolean;
  startError: string | null;
  /** A run belongs to this thread: no answer above it is live any more. */
  runStarted: boolean;
  /** The thread is a conversation with the agent (see above). */
  threaded: boolean;
  /** The run in the URL; its lines show at its marker in the thread. */
  currentRun: string | null;
  /** The name of a run this browser listed, for the marker of a run that is not on screen. */
  runTitle(runId: string): string | null;
  /** "Try again" on a failed fact check is offered: in an agent thread only while the check is the whole conversation. */
  canRetry: boolean;
  /** The run's versions by name ("v4" to its id), so a name in an answer can open it. */
  versions: Readonly<Record<string, string>>;
  onPickVersion(id: string): void;
  /** The answer whose walkthrough is showing, if any. */
  walkTurnId: string | null;
  /** Shows an answer's walkthrough on the Architecture tab. No new model call. */
  onWalk(turnId: string): void;
  /** The chat box is waiting on a run, so a suggestion chip would not send. */
  chipsOff: boolean;
  /** A suggestion chip: sent as the next message. */
  onChip(text: string): void;
  onConfirm(turn: ProposalTurn, req: RunRequest): void;
  onDismiss(turnId: string): void;
  onRun(cardId: string, prompt: string, req: RunRequest): void;
  onPickYourself(prompt: string): void;
  onClosest(prompt: string, chip: Chip): void;
  /** An example chip under "Not a chip": fills the chat box, never sends. */
  onExample(text: string): void;
  /** "Could not check": the same sentence is read again in place of this reply. */
  onRetry(messageId: string, prompt: string): void;
  /** The access code from a reply that needed one; Verity reads the sentence again once it is accepted. */
  onCode(messageId: string, prompt: string, code: string): Promise<string | null>;
  onSubmitCode(code: string): Promise<string | null>;
  /** Changes when something new lands at the bottom, so the thread follows it. */
  scrollKey: string;
  cpuCanBuild: boolean;
  threadId: string | null;
  onCpuStarted(messageId: string, jobId: string): void;
  /** Confirm on the agent's CPU proposal started this job; the board opens in the right panel. */
  onCpuProposalStarted(messageId: string, jobId: string): void;
  onCpuProposalDismiss(messageId: string): void;
  /** "Show board" on a started CPU job: the board opens in the right panel. */
  onShowBoard(jobId: string): void;
  /** The run's own messages (progress and the end), read from the run state. */
  children?: ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  // Follow the newest message to the bottom. One taller than the view is shown from its top, so its first line is not lost.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const newest = el.querySelector(".b-thread")?.lastElementChild;
    const tall = newest instanceof HTMLElement && newest.offsetHeight > el.clientHeight - 64;
    const top = tall ? newest.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 16 : el.scrollHeight;
    el.scrollTo({ top: Math.max(0, top), behavior: reduced ? "auto" : "smooth" });
  }, [scrollKey, reduced]);

  const last = messages.length - 1;
  // Where the run lines go: at the marker of the run in the URL, or after the messages when the thread has no such marker.
  const lineAt = currentRun !== null ? messages.findIndex((m) => m.kind === "run" && m.runId === currentRun) : -1;
  // Agent thread: the newest card, refusal, proposal or first-message failure after the last run is the one with live buttons.
  const lastRun = messages.findLastIndex((m) => m.kind === "run" || (m.kind === "cpu_choice" && !!m.jobId));
  const lastAction = messages.findLastIndex((m) => m.kind === "cpu_choice" || m.kind === "card" || m.kind === "refused" || m.kind === "proposal" || m.kind === "cpu_proposal" || (m.kind === "error" && !m.plain));
  const liveAt = (i: number, m: ChatMessage): boolean => {
    if (!threaded) return i === last && !runStarted;
    if (currentRun !== null && lineAt < 0) return false; // the run on screen is not in this thread's messages
    if (m.kind === "error" && m.plain) return i === last;
    return i === lastAction && i > lastRun;
  };
  return (
    <div className="b-scroll" ref={scrollRef}>
      <div className="b-thread">
        {messages.map((m, i) => {
          const live = liveAt(i, m);
          switch (m.kind) {
            case "cpu_choice":
              return <CpuCard key={m.id} kinds={m.cpuKinds} jobId={m.jobId} live={live && !chipsOff && !needsCode && !starting} canBuild={cpuCanBuild} threadId={threadId} onStarted={(jobId) => onCpuStarted(m.id, jobId)} onShowBoard={onShowBoard} />;
            case "user":
              return (
                <div key={m.id} className="b-me">
                  {m.version ? (
                    <span className="b-me-v m" aria-label={`About ${m.version}`}>
                      {m.version}
                    </span>
                  ) : null}
                  {m.text}
                </div>
              );
            case "parsing":
              return (
                <p key={m.id} className="b-ai b-wait" role="status">
                  <Sq kind="run" />{" "}
                  {m.thinking ? "Verity is thinking..." : m.checking ? "Reading your description and checking it against sources..." : "Reading your description..."}
                </p>
              );
            case "answer":
              return (
                <AgentAnswer
                  key={m.id}
                  m={m}
                  live={i === last}
                  chipsOff={chipsOff}
                  versions={versions}
                  onPickVersion={onPickVersion}
                  canWalk={m.walkRunId !== undefined && m.walkRunId === currentRun}
                  walking={walkTurnId === m.id}
                  onWalk={() => onWalk(m.id)}
                  onChip={onChip}
                />
              );
            case "proposal":
              return (
                <div key={m.id} className="b-ai">
                  {m.toolSteps && m.toolSteps.length > 0 ? <ToolSteps steps={m.toolSteps} /> : null}
                  <p>{withoutCap(m.text)}</p>
                  <ProposalCard
                    turn={m}
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
            case "cpu_proposal":
              return (
                <CpuProposalCard
                  key={m.id}
                  toolSteps={m.toolSteps}
                  text={m.text}
                  action={m.action}
                  state={m.state}
                  jobId={m.jobId}
                  restored={m.restored}
                  live={live && !chipsOff && !needsCode && !starting}
                  threadId={threadId}
                  onStarted={(jobId) => onCpuProposalStarted(m.id, jobId)}
                  onDismiss={() => onCpuProposalDismiss(m.id)}
                  onShowBoard={onShowBoard}
                />
              );
            case "declined":
              return (
                <div key={m.id} className="b-ai">
                  {m.toolSteps && m.toolSteps.length > 0 ? <ToolSteps steps={m.toolSteps} /> : null}
                  <p>{withoutCap(m.text)}</p>
                </div>
              );
            case "run":
              return m.runId === currentRun ? <Fragment key={m.id}>{children}</Fragment> : <RunNote key={m.id} runId={m.runId} title={runTitle(m.runId)} />;
            case "card": {
              const verdict = m.check?.verdict ?? null;
              const research = m.check?.research ?? null;
              // The research sentences sit in Details, so the plain intro is always the reply's words.
              const intro = m.intro ? m.intro : null;
              // What the sentence did not say took the default: said once, as chips, so the reader can change it before Run.
              const assumed = m.assumed ? assumedWords(m.familyId, m.knobs, m.assumed) : [];
              return (
                <div key={m.id} className="b-ai">
                  {m.toolSteps && m.toolSteps.length > 0 ? <ToolSteps steps={m.toolSteps} /> : null}
                  {verdict ? <VerdictLine verdict={verdict} onRetry={live && canRetry && !m.sent && research?.status !== "ok" ? () => onRetry(m.id, m.prompt) : undefined} /> : null}
                  {intro ? <p>{intro}</p> : null}
                  {m.note ? <p className="b-dim">{m.note}</p> : null}
                  {unchecked(m.check) ? <p className="b-note b-dim">Not fact-checked: I could not look this up just now.</p> : null}
                  {assumed.length > 0 ? (
                    <div className="b-assume" aria-label="What I assumed">
                      {assumed.map((a) => (
                        <span key={a} className="b-tag">
                          I assumed: {a}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <ChipCard
                    msg={m}
                    live={live && !m.sent}
                    health={health}
                    needsCode={needsCode}
                    codeNotice={codeNotice}
                    starting={starting}
                    startError={live ? startError : null}
                    onSubmitCode={onSubmitCode}
                    onRun={(req) => onRun(m.id, m.prompt, req)}
                  />
                  {research ? (
                    <ReplyDetails>
                      <ResearchBlock research={research} />
                    </ReplyDetails>
                  ) : null}
                </div>
              );
            }
            case "refused": {
              const name = m.closest ? inSentence(familyById(m.closest.familyId)?.name ?? "chip") : null;
              const verdict = m.check?.verdict ?? null;
              const research = m.check?.research ?? null;
              // Not a chip: a kind sentence and a few chips to try, no closest card unless the server sent one.
              const notChip = verdict === "not_a_chip";
              // Not a chip needs no search: said once, in Details, so the reply has a line for how it was checked.
              const noSearch = notChip && !research;
              return (
                <div key={m.id} className="b-ai">
                  {verdict ? (
                    <>
                      {/* Asking again would pay for the same answer when the sources were found: Try again only follows a failed check. */}
                      <VerdictLine verdict={verdict} onRetry={live && canRetry && research?.status !== "ok" ? () => onRetry(m.id, m.prompt) : undefined} />
                      <p>{m.reason}</p>
                    </>
                  ) : (
                    <p>
                      <b>I can&apos;t build that.</b> {m.reason}
                    </p>
                  )}
                  {m.closest ? (
                    <p>
                      The closest chip I can build is {name}: <b>{m.closest.title}</b>.
                    </p>
                  ) : notChip ? null : (
                    <p>Nothing I can build comes close.</p>
                  )}
                  {live && notChip ? (
                    <>
                      <p className="b-note b-dim">Try one of these instead.</p>
                      <div className="b-acts" role="group" aria-label="Chips to try">
                        {EXAMPLES.map((ex) => (
                          <button key={ex.family} type="button" className="b-chip" title={ex.text} onClick={() => onExample(ex.text)}>
                            {ex.label}
                          </button>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {live ? (
                    <div className="b-acts">
                      {m.closest ? (
                        <button type="button" className="bx ghost" onClick={() => m.closest && onClosest(m.prompt, m.closest)}>
                          Use the closest chip
                        </button>
                      ) : null}
                      <button type="button" className="bx ghost" onClick={() => onPickYourself(m.prompt)}>
                        Pick it yourself
                      </button>
                    </div>
                  ) : null}
                  {/* One Details holds the research, how it was checked and what Verity cannot build. With nothing but the last, that dropdown stands alone. */}
                  {research || noSearch ? (
                    <ReplyDetails>
                      {noSearch ? <p className="b-note b-dim">No search needed: checked against what Verity can build.</p> : null}
                      {research ? <ResearchBlock research={research} /> : null}
                      {notChip ? null : <CannotBuild />}
                    </ReplyDetails>
                  ) : notChip ? null : (
                    <CannotBuild />
                  )}
                </div>
              );
            }
            case "error": {
              // In the agent's thread a failed message can be sent again: a first one on a new thread, a later one on the same thread.
              const retry = live && threaded && !m.needsCode && !m.spendLimit;
              const pick = live && !m.spendLimit && !m.plain;
              return (
                <div key={m.id} className="b-ai">
                  {live && m.needsCode ? <AccessCode onSubmit={(code) => onCode(m.id, m.prompt, code)} notice={m.text} /> : <p role={m.plain ? "alert" : undefined}>{m.text}</p>}
                  {pick ? <p className="b-dim">You can still pick the chip and its settings yourself.</p> : null}
                  {retry || pick ? (
                    <div className="b-acts">
                      {retry ? (
                        <button type="button" className="bx ghost" onClick={() => onRetry(m.id, m.prompt)}>
                          Try again
                        </button>
                      ) : null}
                      {pick ? (
                        <button type="button" className="bx ghost" onClick={() => onPickYourself(m.prompt)}>
                          Pick it yourself
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            }
          }
        })}
        {lineAt < 0 ? children : null}
      </div>
    </div>
  );
}

/**
 * The run in the chat: one short line with two small buttons. The steps, the agent's line, the credits and Reconnect
 * live in the right panel (ProgressTab). On a phone the panel is a sheet, and Show progress is the only way to it.
 */
export function RunningLine({
  run,
  connection,
  wide,
  panelOpen,
  stopping,
  showRef,
  onShow,
  onStop,
}: {
  run: RunState;
  connection: LiveConnection;
  /** The panel sits beside the chat (900px and up), so "on the right" is true. */
  wide: boolean;
  panelOpen: boolean;
  stopping: boolean;
  showRef: RefObject<HTMLButtonElement | null>;
  onShow(): void;
  onStop(): void;
}) {
  // Nothing has arrived from this run yet: no claim that it is building, and nothing to stop.
  const unheard = run.phase === "idle";
  const lost = connection === "error";
  const text = lost
    ? `${unheard ? "Could not open this run." : "Lost the connection to this run. It keeps going on the server."} Open the progress to reconnect.`
    : unheard
      ? "Connecting to the run."
      : wide && panelOpen
        ? "Building it now. The steps are on the right."
        : "Building it now. Open the progress to follow the steps.";
  return (
    <div className="b-ai" data-running="">
      <p>{text}</p>
      <div className="b-acts">
        <button type="button" ref={showRef} className="bx ghost sm" aria-expanded={panelOpen} aria-controls="bench-panel" onClick={onShow}>
          Show progress
        </button>
        {!unheard ? (
          <button type="button" className="bx ghost sm" disabled={stopping} onClick={onStop}>
            {stopping ? "Stopping..." : "Stop"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** How a run ended, in a plain sentence, with what to do next. A finished run also gets the result card. */
export function EndMessage({
  run,
  title,
  priority,
  liveError,
  canRerun,
  needsCode,
  codeNotice,
  starting,
  startError,
  panelOpen,
  resultRef,
  onOpenPanel,
  onRerun,
  onSubmitCode,
}: {
  run: RunState;
  title: string;
  priority: Priority;
  liveError: string | null;
  /** This browser knows what was sent, so the same chip can run again. */
  canRerun: boolean;
  needsCode: boolean;
  codeNotice: string | null;
  starting: boolean;
  startError: string | null;
  panelOpen: boolean;
  resultRef: RefObject<HTMLButtonElement | null>;
  onOpenPanel(): void;
  onRerun(): void;
  onSubmitCode(code: string): Promise<string | null>;
}) {
  const spent = formatUsd(run.credits.usedUsd);
  const hasVersions = run.candidateOrder.length > 0;
  // "Best so far" only when a version passed and was measured; otherwise the result holds what was written.
  const best = run.winnerId ?? run.baselineId;

  const resultCard = (
    <button type="button" ref={resultRef} className="b-outcard" aria-expanded={panelOpen} aria-controls="bench-panel" onClick={onOpenPanel}>
      <span className="b-outcard-t">
        <b>{title}</b>
        <small>{run.phase === "done" ? "Winner, versions and Verilog" : best ? "Best so far, versions and Verilog" : "Versions and Verilog"}</small>
      </span>
      <span className="b-outcard-a">{panelOpen ? "Showing" : "Open"}</span>
    </button>
  );

  const again = (label: string) =>
    needsCode ? (
      <AccessCode onSubmit={onSubmitCode} notice={codeNotice} />
    ) : (
      <button type="button" className="bx ghost" disabled={starting} onClick={onRerun}>
        {starting ? "Starting..." : label}
      </button>
    );

  if (run.phase === "done") {
    return (
      <div className="b-ai">
        <p>
          <b>Done.</b> {doneSentence(run, priority)}
        </p>
        {resultCard}
      </div>
    );
  }

  if (run.phase === "stopped") {
    return (
      <div className="b-ai">
        <p>
          <b>You stopped the run.</b> {spent} spent.{" "}
          {best ? "The best version so far is in the result." : hasVersions ? "No version passed the tests yet. What was written is in the result." : "No version was written."}
        </p>
        {hasVersions ? resultCard : null}
        {canRerun ? <div className="b-acts">{again("Run again")}</div> : null}
        {startError ? <p className="b-note" role="alert">{startError}</p> : null}
      </div>
    );
  }

  // The server writes most failures as one plain sentence meant to be shown as is. A short technical fragment ("internal
  // error", "timed out: ...") is not a sentence, so it stays in the dropdown.
  const failure = run.phase === "error" && liveError ? liveError : null;
  const plain = failure !== null && /^[A-Z].*\.$/.test(failure);
  return (
    <div className="b-ai">
      <p>
        <b>{run.phase === "cap_reached" ? "The run reached its spend limit." : "The run stopped because of a problem."}</b> {plain ? `${failure} ` : ""}
        {spent} spent. No more credits will be spent.
      </p>
      {failure && !plain ? (
        <details className="b-more">
          <summary>What went wrong</summary>
          <p>{failure}</p>
        </details>
      ) : null}
      {hasVersions ? resultCard : null}
      <div className="b-acts">
        {canRerun ? again("Try again") : null}
        <Link prefetch={false} href="/studio?run=mac4-real" className="bx ghost">
          Watch a recorded run
        </Link>
      </div>
      {startError ? <p className="b-note" role="alert">{startError}</p> : null}
    </div>
  );
}
