"use client";

import { CodeDiff } from "@/components/domain/CodeDiff";
import { CodeView } from "@/components/domain/CodeView";
import type { Candidate, Equivalence, PPA, RunState } from "@/lib/contracts/types";
import { formatDelta } from "@/lib/format";
import { Sq } from "../studio/loop/LoopModule";
import { wavesOf } from "./artifacts";
import { TERMINAL } from "./derive";
import { SignoffBlock, type SignoffView } from "./Signoff";
import { Waveform } from "./Waveform";

type Kind = "pass" | "fail" | "run" | "wait" | "drop";
type Badge = { key: string; kind: Kind | null; text: string };

const OPERATOR_WORD: Record<Candidate["createdBy"], string> = { INIT: "first version", SIMPLIFY: "simplify", OPTIMIZE: "optimize", EXPLORE: "explore" };

function stateOf(c: Candidate, run: RunState): { kind: Kind; word: string } {
  const ended = TERMINAL.has(run.phase);
  if (c.id === run.winnerId) return { kind: "pass", word: "Winner" };
  switch (c.status) {
    case "measured":
      return { kind: "pass", word: c.isBaseline ? "Baseline" : "Passed and measured" };
    case "pass":
      return { kind: "pass", word: "Passed" };
    case "fail":
      return { kind: "fail", word: "Failed a test" };
    case "rejected":
      return { kind: "fail", word: "Dropped" };
    default:
      return ended ? { kind: "drop", word: "Not finished" } : { kind: "run", word: "In progress" };
  }
}

function proofBadge(key: string, status: Equivalence["status"] | "v1_is_winner", against: string): Badge | null {
  if (status === "proven") return { key, kind: "pass", text: `Proven equal to ${against}` };
  if (status === "not_equivalent") return { key, kind: "fail", text: `Differs from ${against}` };
  if (status === "inconclusive") return { key, kind: "fail", text: `Not proven equal to ${against}` };
  return null;
}

/** The badges of one version, every one read from the run: tests, seeds, lint, proofs, and area and clock against v1. */
function badgesOf(c: Candidate, run: RunState, signoff: SignoffView, human: { ppa: PPA; eqy: Equivalence } | null): Badge[] {
  const out: Badge[] = [];
  const v = c.verification;
  const sheet = signoff.state === "ok" && signoff.sheet.candidateId === c.id ? signoff.sheet : null;
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const baseLabel = base?.label ?? "v1";
  if (v) out.push({ key: "tests", kind: v.total > 0 && v.passed === v.total ? "pass" : "fail", text: `${v.passed} of ${v.total} tests` });
  if (sheet && sheet.tests.seedsRun > 0) {
    out.push({ key: "seeds", kind: sheet.tests.seedsPassed === sheet.tests.seedsRun ? "pass" : "fail", text: `${sheet.tests.seedsPassed} of ${sheet.tests.seedsRun} seeds` });
  }
  if (sheet) out.push({ key: "lint", kind: sheet.lint.errors === 0 ? "pass" : "fail", text: `Lint ${sheet.lint.errors} errors, ${sheet.lint.warnings} warnings` });
  else if (v) out.push({ key: "lint", kind: v.lint === "pass" ? "pass" : "fail", text: v.lint === "pass" ? "Lint clean" : "Lint failed" });
  const v1 = c.eqy ? proofBadge("eqy", c.eqy.status, baseLabel) : sheet ? proofBadge("eqy", sheet.eqyVsV1, baseLabel) : null;
  if (v1) out.push(v1);
  const humanStatus = sheet?.eqyVsHuman ?? (c.id === run.winnerId ? human?.eqy.status : undefined);
  const h = humanStatus ? proofBadge("human", humanStatus, "the human design") : null;
  if (h) out.push(h);
  if (c.ppa && base?.ppa && c.id !== base.id) {
    const a = c.ppa.areaUm2;
    const k = c.ppa.clockPeriodNs;
    if (a !== null && base.ppa.areaUm2) out.push({ key: "area", kind: null, text: `Area ${formatDelta(a, base.ppa.areaUm2, { lowerIsBetter: true }).text} vs ${baseLabel}` });
    if (k !== null && base.ppa.clockPeriodNs) out.push({ key: "clock", kind: null, text: `Clock period ${formatDelta(k, base.ppa.clockPeriodNs, { lowerIsBetter: true }).text} vs ${baseLabel}` });
  } else if (c.ppa && c.id === base?.id) {
    out.push({ key: "base", kind: null, text: "Every number is compared to this version" });
  }
  return out;
}

/**
 * The tab for one version ("v4"), opened by picking it anywhere: its badges, why the agent wrote it, its waveform, the
 * sign-off sheet when it is the winner, the change from its parent and its Verilog. Nothing here is computed beyond
 * reading and formatting run data.
 */
export function VersionTab({
  run,
  c,
  human,
  signoff,
  attached,
  canAsk,
  onAttach,
  onPickVersion,
}: {
  run: RunState;
  c: Candidate;
  human: { ppa: PPA; eqy: Equivalence } | null;
  signoff: SignoffView;
  /** This version is the one on the chat box. */
  attached: boolean;
  /** Questions about this run can be asked (the run ended and the server answers them). */
  canAsk: boolean;
  onAttach(): void;
  onPickVersion(id: string): void;
}) {
  const st = stateOf(c, run);
  const parent = c.parentId ? (run.candidates[c.parentId] ?? null) : null;
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const waves = wavesOf(run, c.id);
  const badges = badgesOf(c, run, signoff, human);
  const isWinner = c.id === run.winnerId;
  return (
    <div className="b-ver">
      <div className="b-ver-h">
        <p className="b-ver-t">
          <b>{c.label}</b>
          <span>
            {OPERATOR_WORD[c.createdBy]}
            {parent ? (
              <>
                {" "}
                from{" "}
                <button type="button" className="b-vlink" onClick={() => onPickVersion(parent.id)}>
                  {parent.label}
                </button>
              </>
            ) : null}
          </span>
        </p>
        <span className="b-st">
          <Sq kind={st.kind} /> {st.word}
        </span>
      </div>
      {canAsk ? (
        attached ? (
          <p className="b-note b-dim">On the chat box: your next question is about {c.label}.</p>
        ) : (
          <button type="button" className="bx ghost sm b-ver-ask" onClick={onAttach}>
            Ask about {c.label}
          </button>
        )
      ) : null}
      {badges.length > 0 ? (
        <ul className="b-badges" aria-label={`${c.label} at a glance`}>
          {badges.map((b) => (
            <li key={b.key}>
              {b.kind ? <Sq kind={b.kind} /> : null}
              {b.text}
            </li>
          ))}
        </ul>
      ) : null}
      {c.reason ? <p className="b-ver-why">{c.reason}</p> : null}
      {c.fixCount > 0 ? <p className="b-note b-dim">The agent fixed it {c.fixCount === 1 ? "once" : `${c.fixCount} times`} before it passed.</p> : null}
      {waves.state === "ok" ? <Waveform waves={waves.value} label={c.label} /> : waves.state === "bad" ? <p className="b-note b-dim">The waveform came, but it could not be read.</p> : null}
      {isWinner && signoff.state !== "unsupported" ? <SignoffBlock view={signoff} baseLabel={base?.label ?? "v1"} /> : null}
      {parent && c.rtl ? (
        <details className="b-more">
          <summary>Changes from {parent.label}</summary>
          <CodeDiff before={parent.rtl} after={c.rtl} beforeLabel={parent.label} afterLabel={c.label} />
        </details>
      ) : null}
      {c.rtl ? (
        <details className="b-more" open>
          <summary>Verilog</summary>
          <CodeView code={c.rtl} language="verilog" filename={`${c.label}.v`} />
        </details>
      ) : (
        <p className="b-empty">{c.label} has no Verilog yet.</p>
      )}
    </div>
  );
}
