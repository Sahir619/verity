"use client";

import { Fragment, useState } from "react";
import { CodeDiff } from "@/components/domain/CodeDiff";
import { CodeView } from "@/components/domain/CodeView";
import { ExpectedVsGotTable } from "@/components/domain/ExpectedVsGotTable";
import { FitnessBreakdown } from "@/components/domain/FitnessBreakdown";
import { PpaTable } from "@/components/domain/PpaTable";
import { formatScore } from "@/components/domain/signed";
import type { Attempt, Candidate, RunState } from "@/lib/contracts/types";
import type { SelectedEntity } from "@/lib/contracts/view-types";
import { useLive } from "@/lib/store";
import { fixedText, rejectedText } from "../figure/derive";
import { proofText } from "../figure/proof";

type CandidateEntity = Extract<SelectedEntity, { kind: "candidate" }>;

/** Statuses where measurement applies: passed (measured or about to be), or rejected (never measured). */
const MEASURABLE = new Set<Candidate["status"]>(["pass", "synthesizing", "measured", "rejected"]);

const RUNNING_WORD: Partial<Record<Candidate["status"], string>> = {
  drafting: "writing the RTL",
  linting: "linting with Verilator",
  simulating: "running the locked tests",
  fixing: "writing the fix",
};

/** The status as a marker square plus words. Never colour alone. */
function statusLine(c: Candidate): { tone: "pass" | "fail" | "info"; text: string } {
  const v = c.verification;
  const tests = v ? `${v.passed} of ${v.total}` : "";
  switch (c.status) {
    case "drafting":
      return { tone: "info", text: "Writing RTL" };
    case "linting":
      return { tone: "info", text: "Linting" };
    case "simulating":
      return { tone: "info", text: "Simulating" };
    case "fail":
      return { tone: "fail", text: `Fail, ${tests} passed` };
    case "fixing":
      return { tone: "fail", text: `Fixing, ${tests} passed` };
    case "pass":
      return { tone: "pass", text: `Pass, ${tests}` };
    case "synthesizing":
      return { tone: "info", text: `Pass, ${tests}. Measuring` };
    case "measured":
      return { tone: "pass", text: `Pass, ${tests}. Measured` };
    case "rejected":
      return { tone: "fail", text: rejectedText(c) };
  }
}

function FixStep({ from, to }: { from: Attempt; to: Attempt }) {
  return (
    <div className="dp-fix">
      <h3 className="dp-h">
        Fix, attempt {from.n} to {to.n}
      </h3>
      {to.reason ? <p className="dp-p">{to.reason}</p> : null}
      <CodeDiff before={from.rtl} after={to.rtl} beforeLabel={`attempt ${from.n}`} afterLabel={`attempt ${to.n}`} />
    </div>
  );
}

function WinnerOverride({ c, winner, mode }: { c: Candidate; winner: Candidate | null; mode: RunState["mode"] }) {
  const { steer, overrideWinnerLocal } = useLive();
  const [confirming, setConfirming] = useState(false);
  const confirm = () => {
    if (mode === "live") void steer({ kind: "winner_override", candidateId: c.id });
    else overrideWinnerLocal(c.id);
    setConfirming(false);
  };
  if (!confirming) {
    return (
      <button type="button" className="btn ghost xs" onClick={() => setConfirming(true)}>
        Make {c.label} the winner
      </button>
    );
  }
  return (
    <div className="dp-confirm" role="group" aria-label="Confirm winner change">
      <p className="dp-p">
        Make {c.label} the winner? Its fitness is {c.fitness ? formatScore(c.fitness.score) : "not scored"};{" "}
        {winner ? `${winner.label} has ${winner.fitness ? formatScore(winner.fitness.score) : "no score"}` : "there is no winner yet"}.
      </p>
      <div className="dp-acts">
        <button type="button" className="btn sm" onClick={confirm}>
          Confirm
        </button>
        <button type="button" className="btn ghost sm" onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * One version, as a story: who made it and why, every attempt (failing cases, the agent's diagnosis, the fix as a
 * diff), then the measurements against v1 and the equivalence result. Every value comes from the run record.
 */
export function CandidateDetail({ entity, run }: { entity: CandidateEntity; run: RunState }) {
  const c = entity.candidate;
  const [showParent, setShowParent] = useState(false);
  const [showCode, setShowCode] = useState(false);

  const parent = c.parentId ? (run.candidates[c.parentId] ?? null) : null;
  const baselineId = run.baselineId;
  const baselineLabel = baselineId ? (run.candidates[baselineId]?.label ?? "v1") : "v1";
  const winner = run.winnerId ? (run.candidates[run.winnerId] ?? null) : null;
  const status = statusLine(c);
  const running = RUNNING_WORD[c.status];
  const fixed = c.status === "rejected" ? null : fixedText(c.fixCount);
  const tags: string[] = [];
  if (fixed) tags.push(fixed);
  if (c.isBaseline) tags.push("Baseline");
  if (c.isElite && !entity.isWinner) tags.push("Elite");
  if (entity.isWinner) tags.push(entity.winnerBy === "user" ? "Winner (your pick)" : "Winner");

  const ppaSource = c.ppa ?? (baselineId ? run.candidates[baselineId]?.ppa : null) ?? null;
  const tools = ppaSource ? [...ppaSource.tools, ppaSource.pdk].filter(Boolean).join(", ") : null;
  const proveActive = run.stages.prove.status === "active";
  const proof = c.eqy ? proofText(c.eqy, baselineLabel) : null;

  return (
    <>
      <header className="dp-hero">
        <h2 className="dp-title">
          <span className="lbl">{c.label}</span>
          <span className="by">
            {c.createdBy}
            {entity.parentLabel ? ` from ${entity.parentLabel}` : ""}
          </span>
        </h2>
        <p className={`mk ${status.tone}`}>{status.text}</p>
        {c.reason ? <p className="dp-reason">{c.reason}</p> : null}
        {tags.length > 0 ? (
          <ul className="dp-tags" aria-label="Tags">
            {tags.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : null}
        {entity.winnerBy === "user" && run.mode === "replay" ? (
          <p className="dp-p dp-mint">
            Local to this replay. Restarting the replay resets it.{entity.aiWinnerLabel ? ` AI pick: ${entity.aiWinnerLabel}.` : ""}
          </p>
        ) : null}
      </header>

      <section className="dp-sec" aria-label="Attempts">
        {c.attempts.map((a, i) => (
          <Fragment key={a.n}>
            {i > 0 ? <FixStep from={c.attempts[i - 1]} to={a} /> : null}
            <ExpectedVsGotTable verification={a.verification} attemptLabel={`Attempt ${a.n}`} />
          </Fragment>
        ))}
        {running ? (
          <p className="dp-run">
            <span className="sq" aria-hidden />
            Attempt {c.attempts.length + 1}: {running}.
          </p>
        ) : null}
      </section>

      {MEASURABLE.has(c.status) ? (
        <section className="dp-sec">
          <h3 className="dp-h">Measured against {baselineLabel}</h3>
          {c.status === "synthesizing" && !c.ppa ? (
            <p className="dp-run">
              <span className="sq" aria-hidden />
              Measuring area, timing and power.
            </p>
          ) : (
            <PpaTable ppa={c.ppa} baseline={entity.baseline} status={c.status} isBaseline={c.isBaseline} />
          )}
          {c.ppa ? <FitnessBreakdown fitness={c.fitness} status={c.status} /> : null}
        </section>
      ) : null}

      {proof ? (
        <section className="dp-sec">
          <h3 className="dp-h">Equivalence</h3>
          <p className="prove-r" data-r={proof.result}>
            {proof.label}
          </p>
          <p className="dp-p">{proof.sentence}</p>
          {c.eqy?.log ? <pre className="cli dp-log">{c.eqy.log}</pre> : null}
        </section>
      ) : entity.isWinner ? (
        <section className="dp-sec">
          <h3 className="dp-h">Equivalence</h3>
          {proveActive ? (
            <p className="prove-r" data-r="run">
              checking against {baselineLabel} with Yosys EQY
            </p>
          ) : (
            <p className="dp-p">Not checked yet. The winner is checked against {baselineLabel} with Yosys EQY.</p>
          )}
        </section>
      ) : null}

      {c.status === "measured" && !entity.isWinner ? (
        <section className="dp-sec">
          <WinnerOverride c={c} winner={winner} mode={run.mode} />
        </section>
      ) : null}

      {c.rtl || parent ? (
        <section className="dp-sec">
          <h3 className="dp-h">Code</h3>
          <div className="dp-acts">
            {parent && entity.parentLabel ? (
              <button type="button" className="btn ghost xs" aria-expanded={showParent} onClick={() => setShowParent((v) => !v)}>
                {showParent ? "Hide" : "Show"} change from {entity.parentLabel}
              </button>
            ) : null}
            {c.rtl ? (
              <button type="button" className="btn ghost xs" aria-expanded={showCode} onClick={() => setShowCode((v) => !v)}>
                {showCode ? "Hide full code" : "Show full code"}
              </button>
            ) : null}
          </div>
          {showParent && parent && entity.parentLabel ? (
            <CodeDiff before={parent.rtl} after={c.attempts[0]?.rtl ?? c.rtl} beforeLabel={entity.parentLabel} afterLabel={c.label} />
          ) : null}
          {showCode && c.rtl ? <CodeView code={c.rtl} language="verilog" filename={`${c.label}.v`} /> : null}
        </section>
      ) : null}

      <footer className="dp-note">
        <p>
          Relative to {baselineLabel}. Open tools, not signoff{tools ? `: ${tools}` : ""}.
        </p>
        {run.sample ? <span className="stamp">SAMPLE DATA</span> : null}
      </footer>
    </>
  );
}
