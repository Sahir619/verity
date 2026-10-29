"use client";

import { useRef, useState } from "react";
import type { ToolStep } from "@/lib/contracts/agent";
import type { CpuAction } from "@/lib/contracts/cpu";
import { formatUsd } from "@/lib/format";
import { dispatchProfileChange, postCpuJob, postCpuRetry, postCpuUpgrade, type CpuJobResult } from "@/lib/sources/live";
import { ToolSteps } from "./AgentTurn";

const CONFIRM_WORD: Record<CpuAction["type"], string> = { new_job: "Confirm: start the CPU job", retry_core: "Confirm: retry the core", upgrade: "Confirm: build CPU v2" };
const PENDING_WORD: Record<CpuAction["type"], string> = { new_job: "Starting the CPU job...", retry_core: "Starting the core retry...", upgrade: "Starting CPU v2..." };

/** The one API call a Confirm makes. A proposal missing the id it needs fails with a sentence instead of calling anything. */
function startJob(a: CpuAction, threadId: string | null): Promise<CpuJobResult> {
  if (a.type === "new_job") return postCpuJob(a.goal, threadId ?? undefined);
  if (a.type === "retry_core" && a.jobId) return postCpuRetry(a.jobId);
  if (a.type === "upgrade" && a.jobId && a.upgradeId) return postCpuUpgrade(a.jobId, a.upgradeId);
  return Promise.resolve({ ok: false, error: "That proposal is incomplete. Ask the agent again." });
}

/**
 * A CPU job the agent proposes. Everything on the card but the agent's own words is written by code. Only Confirm calls the
 * API. A card that is not the newest, or that came back from an earlier load of the chat, never asks for Confirm: ask the
 * agent again for a fresh one, so an old card cannot start a second paid job.
 */
export function CpuProposalCard({ toolSteps, text, action, state, jobId, restored, live, threadId, onStarted, onDismiss, onShowBoard }: {
  toolSteps?: ToolStep[]; text: string; action: CpuAction; state: "open" | "dismissed" | "started"; jobId?: string; restored?: boolean; live: boolean; threadId: string | null;
  onStarted(jobId: string): void; onDismiss(): void; onShowBoard(jobId: string): void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await startJob(action, threadId);
      if (result.ok) {
        onStarted(result.job.id);
        dispatchProfileChange();
      } else setError(result.error);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }
  const actionable = state === "open" && live && !restored;
  return (
    <div className="b-ai">
      {toolSteps && toolSteps.length > 0 ? <ToolSteps steps={toolSteps} /> : null}
      <p>{text}</p>
      <section className="rounded-lg border border-border bg-card p-4 text-card-foreground" aria-label={`Proposed CPU job: ${action.title}`}>
        <h2 className="text-lg font-semibold">{action.title}</h2>
        <p className="mt-1 text-sm">{action.summary}</p>
        <p className="mt-1 text-sm text-muted-foreground">Goal: {action.goal}. Spend cap: <span className="font-mono tabular-nums">{formatUsd(action.maxUsd)}</span></p>
        {state === "started" ? (
          <p className="mt-3 text-sm" role="status">
            <span aria-hidden>■</span> Started.{" "}
            {jobId ? <button type="button" className="underline focus-visible:outline-2 focus-visible:outline-ring" aria-controls="bench-panel" onClick={() => onShowBoard(jobId)}>Open the CPU job</button> : null}
          </p>
        ) : state === "dismissed" ? (
          <p className="mt-3 text-sm text-muted-foreground"><span aria-hidden>□</span> Not now.</p>
        ) : actionable ? (
          <>
            {error ? <p role="alert" className="mt-3 text-sm">{error}</p> : null}
            <div className="b-acts mt-3">
              <button type="button" className="bx pri" disabled={pending} onClick={() => void confirm()}>{pending ? PENDING_WORD[action.type] : CONFIRM_WORD[action.type]}</button>
              <button type="button" className="bx ghost" disabled={pending} onClick={onDismiss}>Dismiss</button>
            </div>
          </>
        ) : (
          <p className="mt-3 text-sm text-muted-foreground"><span aria-hidden>□</span> Proposal from earlier in this chat. Ask the agent again for a fresh one.</p>
        )}
      </section>
    </div>
  );
}
