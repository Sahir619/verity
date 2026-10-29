"use client";

import { useMemo, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { Maximize2, Minimize2 } from "lucide-react";
import type { WalkStep } from "@/lib/contracts/ask";
import type { Candidate, Equivalence, PPA, RunState } from "@/lib/contracts/types";
import { formatArea, formatDelta, formatNs, formatTests } from "@/lib/format";
import { useVerityStore } from "@/lib/store";
import { Sq } from "../studio/loop/LoopModule";
import { circuitOf, specRulesOf, synthReportOf, timingReportOf, wavesOf, type Circuit } from "./artifacts";
import { draftsLine, railSteps, RAIL_TITLES, type RailId, type RailStep } from "./rail";
import { SignoffBlock, type SignoffView } from "./Signoff";
import { Waveform } from "./Waveform";

// The circuit view draws in the browser only (it measures the page), and loads when the tab first shows.
const CircuitView = dynamic(() => import("./CircuitView").then((m) => m.CircuitView), {
  ssr: false,
  loading: () => <div className="c-wrap c-loading" aria-hidden />,
});

/** A walkthrough from an answer: its steps, where it is, and how to move. Moving never calls the model. */
export type Walk = { steps: WalkStep[]; index: number; onBack(): void; onNext(): void; onEnd(): void };

const OPERATOR_WORD: Record<Candidate["createdBy"], string> = { INIT: "first version", SIMPLIFY: "simplify", OPTIMIZE: "optimize", EXPLORE: "explore" };

function statusWord(c: Candidate, run: RunState): { kind: "pass" | "fail" | "run" | "wait" | "drop"; word: string } {
  if (c.id === run.winnerId) return { kind: "pass", word: "Winner" };
  switch (c.status) {
    case "measured":
      return c.isBaseline ? { kind: "pass", word: "Baseline" } : { kind: "pass", word: "Measured" };
    case "pass":
      return { kind: "pass", word: "Passed" };
    case "rejected":
      return { kind: "fail", word: "Dropped" };
    case "fail":
      return { kind: "fail", word: "Failed a test" };
    default:
      return { kind: "run", word: "Not finished" };
  }
}

/** A version's change against v1 in words, e.g. "area -1.6% vs v1". Null when either side was not measured. */
function vsBase(name: string, v: number | null, b: number | null | undefined, baseLabel: string): string | null {
  return v !== null && b ? `${name} ${formatDelta(v, b, { lowerIsBetter: true }).text} vs ${baseLabel}` : null;
}

/** The evidence behind one step, read from the run. */
function Evidence({
  id,
  run,
  c,
  human,
  signoff,
  assumed,
  onPickVersion,
}: {
  id: RailId;
  run: RunState;
  c: Candidate | null;
  human: { ppa: PPA; eqy: Equivalence } | null;
  signoff: SignoffView;
  assumed: string[] | null;
  onPickVersion(id: string): void;
}) {
  const log = useVerityStore((s) => s.log);
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const baseLabel = base?.label ?? "v1";
  switch (id) {
    case "spec": {
      const rules = specRulesOf(run);
      return (
        <>
          {rules.state === "ok" ? (
            <>
              <p className="b-note">The rules of the locked pack, which every version must follow:</p>
              <ul className="b-more-list">
                {rules.value.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="b-note b-dim">{rules.state === "bad" ? "The pack's rules came, but they could not be read." : "This run did not send the pack's rules."}</p>
          )}
          {assumed && assumed.length > 0 ? (
            <div className="b-assume" aria-label="What the chat assumed">
              {assumed.map((a) => (
                <span key={a} className="b-tag">
                  I assumed: {a}
                </span>
              ))}
            </div>
          ) : null}
          {run.spec ? (
            <details className="b-more">
              <summary>The spec, as the agent read it</summary>
              <p>{run.spec.text}</p>
              <p className="m">{run.spec.ports.map((p) => `${p.dir} ${p.name}${p.count ? `[${p.count}]` : ""} ${p.width}b`).join(", ")}</p>
            </details>
          ) : null}
        </>
      );
    }
    case "plan":
      return run.plan.blocks.length === 0 ? (
        <p className="b-note b-dim">No plan yet.</p>
      ) : (
        <>
          <p className="b-note">
            <b>Plan order, not wires.</b> The plan is an ordered list of the parts of one module. It does not say how they connect, so no lines are drawn
            between them. The circuit above shows the real connections Yosys found.
          </p>
          <ol className="b-blocks">
            {run.plan.blocks.map((b) => (
              <li key={b.id}>
                <b>{b.name}</b>
                {b.reason ? <span>{b.reason}</span> : null}
              </li>
            ))}
          </ol>
          {run.plan.revision > 1 ? <p className="b-note b-dim">Plan revision {run.plan.revision}.</p> : null}
        </>
      );
    case "rtl": {
      const drafts = draftsLine(log);
      const v1 = base ?? null;
      return (
        <>
          {drafts ? <p className="b-note">{drafts}</p> : null}
          {v1 ? (
            <p className="b-note">
              {v1.label} was written from the plan{v1.fixCount > 0 ? ` and fixed ${v1.fixCount === 1 ? "once" : `${v1.fixCount} times`} by the agent` : ""}. Every later version
              starts from a version that passed.
            </p>
          ) : (
            <p className="b-note b-dim">No version has passed the tests yet.</p>
          )}
          {v1 ? (
            <button type="button" className="bx ghost sm" onClick={() => onPickVersion(v1.id)}>
              Open {v1.label}
            </button>
          ) : null}
        </>
      );
    }
    case "lint": {
      if (!c) return <p className="b-note b-dim">No version yet.</p>;
      const v = c.verification;
      const sheet = signoff.state === "ok" && signoff.sheet.candidateId === c.id ? signoff.sheet : null;
      const tags = sheet ? Object.entries(sheet.lint.byTag).sort((a, b) => b[1] - a[1]) : [];
      return (
        <>
          <p className="b-note">
            {!v ? `${c.label} has not been linted.` : v.lint === "pass" ? `Verilator found no errors in ${c.label}.` : `Verilator found errors in ${c.label}.`}
          </p>
          {sheet ? (
            <p className="b-note">
              Sign-off lint: {sheet.lint.errors} errors, {sheet.lint.warnings} warnings{tags.length ? `: ${tags.map(([t, n]) => `${t} ${n}`).join(", ")}` : ""}. No
              warning is hidden.
            </p>
          ) : (
            <p className="b-note b-dim">Warnings by tag come with the sign-off sheet, for the winner.</p>
          )}
          {v?.lintLog ? (
            <details className="b-more">
              <summary>The lint log</summary>
              <pre className="b-log">{v.lintLog}</pre>
            </details>
          ) : null}
        </>
      );
    }
    case "simulate": {
      if (!c) return <p className="b-note b-dim">No version yet.</p>;
      const v = c.verification;
      const waves = wavesOf(run, c.id);
      const failures = v?.failures ?? [];
      return (
        <>
          <p className="b-note">
            {v ? `${c.label}: ${formatTests(v.passed, v.total)} locked tests passed (${v.tool}).` : `${c.label} has not run the locked tests.`}
          </p>
          {failures.length > 0 ? (
            <ul className="b-fails">
              {failures.slice(0, 5).map((f, i) => (
                <li key={i}>
                  <b>{f.name}</b>
                  {f.cycle !== undefined ? `, cycle ${f.cycle}` : ""}: expected <span className="m">{f.expected}</span>, got <span className="m">{f.got}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {waves.state === "ok" ? (
            <Waveform waves={waves.value} label={c.label} />
          ) : (
            <p className="b-note b-dim">{waves.state === "bad" ? "The waveform came, but it could not be read." : `No waveform for ${c.label}.`}</p>
          )}
        </>
      );
    }
    case "synth": {
      if (!c?.ppa) return <p className="b-note b-dim">{c ? `${c.label} was not measured. Only versions that pass the tests are measured.` : "No version yet."}</p>;
      const synth = synthReportOf(run, c);
      const sta = timingReportOf(run, c);
      const area = vsBase("area", c.ppa.areaUm2, base?.ppa?.areaUm2, baseLabel);
      const clock = vsBase("clock period", c.ppa.clockPeriodNs, base?.ppa?.clockPeriodNs, baseLabel);
      return (
        <>
          <dl className="b-rows">
            <div>
              <dt>Area</dt>
              <dd className="m">
                {c.ppa.areaUm2 !== null ? formatArea(c.ppa.areaUm2) : "not measured"}
                {area && c.id !== base?.id ? <small>{area}</small> : null}
              </dd>
            </div>
            <div>
              <dt>Clock period</dt>
              <dd className="m">
                {c.ppa.clockPeriodNs !== null ? formatNs(c.ppa.clockPeriodNs) : "not measured"}
                {clock && c.id !== base?.id ? <small>{clock}</small> : null}
              </dd>
            </div>
            <div>
              <dt>Cells</dt>
              <dd className="m">{c.ppa.cellCount !== null ? c.ppa.cellCount.toLocaleString("en-US") : "not counted"}</dd>
            </div>
          </dl>
          {synth && synth.top.length > 0 ? <p className="b-note">Most used cells: {synth.top.map((t) => `${t.cell} ${t.count}`).join(", ")}.</p> : null}
          {sta?.start && sta.end ? (
            <p className="b-note">
              Slowest path: from {sta.start} to {sta.end}
              {sta.arrivalNs !== null ? `, data arrives at ${sta.arrivalNs} ns` : ""}.
            </p>
          ) : null}
          <p className="b-note b-dim">Yosys and OpenSTA, post-synthesis estimate, no wires.</p>
          {synth || sta ? (
            <details className="b-more">
              <summary>The reports</summary>
              {synth ? <pre className="b-log">{synth.text}</pre> : null}
              {sta ? <pre className="b-log">{sta.text}</pre> : null}
            </details>
          ) : null}
        </>
      );
    }
    case "improve": {
      const list = run.candidateOrder.map((cid) => run.candidates[cid]).filter((x): x is Candidate => x !== undefined);
      return list.length === 0 ? (
        <p className="b-note b-dim">No versions yet.</p>
      ) : (
        <ul className="b-vlist">
          {list.map((x) => {
            const st = statusWord(x, run);
            const parent = x.parentId ? run.candidates[x.parentId]?.label : null;
            const area = x.id !== base?.id ? vsBase("area", x.ppa?.areaUm2 ?? null, base?.ppa?.areaUm2, baseLabel) : null;
            return (
              <li key={x.id}>
                <button type="button" className="b-vlink" onClick={() => onPickVersion(x.id)}>
                  {x.label}
                </button>
                <span>
                  {OPERATOR_WORD[x.createdBy]}
                  {parent ? ` from ${parent}` : ""}
                  {area ? `, ${area}` : ""}
                </span>
                <span className="b-st">
                  <Sq kind={st.kind} /> {st.word}
                </span>
              </li>
            );
          })}
        </ul>
      );
    }
    case "equivalence": {
      const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
      const eqy = winner?.eqy;
      const words = (s: Equivalence["status"], against: string) =>
        s === "proven"
          ? `proven to work the same as ${against} for every input`
          : s === "not_equivalent"
            ? `differs from ${against}: the checker found an input where they disagree`
            : s === "inconclusive"
              ? `not proven equal to ${against}: the proof did not finish`
              : `not checked against ${against}`;
      return (
        <>
          {winner && eqy ? (
            <p className="b-note">
              <Sq kind={eqy.status === "proven" ? "pass" : "fail"} /> {winner.label} is {words(eqy.status, baseLabel)} (Yosys EQY).
            </p>
          ) : (
            <p className="b-note b-dim">{winner?.id === base?.id && winner ? `${winner.label} is the winner, so there is nothing to prove against it.` : "No proof yet."}</p>
          )}
          {human ? (
            <p className="b-note">
              <Sq kind={human.eqy.status === "proven" ? "pass" : "fail"} /> Against the human-written design: {words(human.eqy.status, "it")}.
            </p>
          ) : null}
          {eqy?.log ? (
            <details className="b-more">
              <summary>The proof log</summary>
              <pre className="b-log">{eqy.log}</pre>
            </details>
          ) : null}
        </>
      );
    }
    case "signoff":
      return <SignoffBlock view={signoff} baseLabel={baseLabel} />;
    case "assertions":
      return <p className="b-note b-dim">Verity does not write or prove assertions yet. Engineers add them before sign-off; it is planned for after the hackathon.</p>;
    case "coverage":
      return <p className="b-note b-dim">Verity does not measure coverage yet. The locked tests are the only stimulus; more seeds run in the sign-off checks.</p>;
  }
}

function CircuitBlock({ run, c, agentIds, big, note }: { run: RunState; c: Candidate | null; agentIds: string[]; big: boolean; note: ReactNode }) {
  const read = c ? circuitOf(run, c.id) : null;
  const circuit: Circuit | null = read?.state === "ok" ? read.value : null;
  const stats = circuit?.stats;
  const merged = circuit?.nodes.some((n) => /\bx\d+$/.test(n.label)) ?? false;
  const summary = stats
    ? [
        stats.regs !== undefined ? `${stats.regs} ${stats.regs === 1 ? "register" : "registers"}` : null,
        stats.adders !== undefined ? `${stats.adders} ${stats.adders === 1 ? "adder" : "adders"}` : null,
        stats.multipliers !== undefined ? `${stats.multipliers} ${stats.multipliers === 1 ? "multiplier" : "multipliers"}` : null,
        stats.muxes !== undefined ? `${stats.muxes} ${stats.muxes === 1 ? "mux" : "muxes"}` : null,
      ].filter(Boolean)
    : [];
  return (
    <section className="b-arch-c" aria-label="Circuit">
      <div className="b-arch-ch">
        <h3 className="b-sec-h">{c ? `Circuit of ${c.label}` : "Circuit"}</h3>
        {note}
      </div>
      {!c ? (
        <p className="b-empty">No version yet. The circuit appears once a version passes the tests and is measured.</p>
      ) : circuit ? (
        <>
          {summary.length > 0 ? <p className="b-note b-dim">{summary.join(", ")}, as Yosys reads the Verilog before it maps it to cells.</p> : null}
          <CircuitView circuit={circuit} agent={agentIds} label={`Circuit of ${c.label}`} big={big} />
          {circuit.truncated ? (
            <p className="b-note">
              <Sq kind="fail" /> Simplified: this circuit has more than 60 parts, so similar parts are merged and the rest are not drawn.
            </p>
          ) : merged ? (
            <p className="b-note b-dim">A part marked x3 stands for three of the same part.</p>
          ) : null}
          <p className="b-note b-dim">Thicker lines carry more than 1 bit. Dashed outline: the walkthrough&apos;s parts. Solid outline: the part you picked.</p>
        </>
      ) : (
        <p className="b-empty">
          {read?.state === "bad"
            ? `The circuit of ${c.label} came, but it could not be read.`
            : c.ppa
              ? `No circuit came for ${c.label}.`
              : `${c.label} was not measured, so there is no circuit. Only versions that pass the tests are measured.`}
        </p>
      )}
    </section>
  );
}

/**
 * The Architecture tab: the engineer's steps as a rail (each a shape and a word, each opening its evidence), and the real
 * circuit of the version in view. A walkthrough from an answer moves its own highlight on the rail and the circuit; the
 * reader's own picks are separate and stay. `big`: the expanded view in the main area, with the rail beside the circuit.
 */
export function ArchitectureView({
  run,
  version,
  human,
  signoff,
  assumed,
  walk,
  big,
  onPickVersion,
  onExpand,
}: {
  run: RunState;
  /** The version the reader picked, else the settled one (the winner, else v1). */
  version: Candidate | null;
  human: { ppa: PPA; eqy: Equivalence } | null;
  signoff: SignoffView;
  /** What the chat took for the knobs the first message did not state, in words. */
  assumed: string[] | null;
  walk: Walk | null;
  big: boolean;
  onPickVersion(id: string): void;
  /** Expand in the panel, Back to chat in the big view. */
  onExpand(): void;
}) {
  const log = useVerityStore((s) => s.log);
  const [open, setOpen] = useState<RailId | null>(null);
  const step = walk ? walk.steps[walk.index] : null;
  // The walkthrough may be about another version than the one picked: it shows its own, and says so.
  const walkVersion = step?.candidateId ? (run.candidates[step.candidateId] ?? Object.values(run.candidates).find((x) => x.label === step.candidateId) ?? null) : null;
  const shown = walkVersion ?? version;
  const rules = useMemo(() => {
    const r = specRulesOf(run);
    return r.state === "ok" ? r.value.length : null;
  }, [run]);
  const steps: RailStep[] = useMemo(
    () => railSteps(run, shown, { signoff, human, drafts: draftsLine(log), rules }),
    [run, shown, signoff, human, log, rules],
  );

  // While a step of the walkthrough is about another version, the picker shows that one and waits (the circuit says so too).
  const overridden = walkVersion !== null && walkVersion.id !== version?.id;
  const picker =
    run.candidateOrder.length > 0 ? (
      <label className="b-pick">
        <span>Version</span>
        <select
          value={shown?.id ?? ""}
          disabled={overridden}
          title={overridden ? "The walkthrough is showing this version. End the walkthrough to pick another." : undefined}
          onChange={(e) => onPickVersion(e.target.value)}
        >
          {shown ? null : <option value="">Pick one</option>}
          {run.candidateOrder.map((cid) => {
            const x = run.candidates[cid];
            return x ? (
              <option key={cid} value={cid}>
                {x.label}
                {cid === run.winnerId ? " (winner)" : ""}
              </option>
            ) : null;
          })}
        </select>
      </label>
    ) : null;

  const walkCard =
    walk && step ? (
      <section className="b-walk" aria-label="Walkthrough" aria-live="polite">
        <p className="b-walk-n">
          Walkthrough, step {walk.index + 1} of {walk.steps.length} <span aria-hidden>·</span> {RAIL_TITLES[step.stepId]}
        </p>
        <p className="b-walk-t">{step.title}</p>
        <p className="b-walk-x">{step.text}</p>
        <div className="b-acts">
          <button type="button" className="bx ghost sm" onClick={walk.onBack} disabled={walk.index === 0}>
            Back
          </button>
          <button type="button" className="bx ghost sm" onClick={walk.onNext} disabled={walk.index === walk.steps.length - 1}>
            Next
          </button>
          <button type="button" className="bx ghost sm" onClick={() => setOpen(step.stepId)} disabled={open === step.stepId}>
            Show this step
          </button>
          <button type="button" className="b-link" onClick={walk.onEnd}>
            End the walkthrough
          </button>
        </div>
      </section>
    ) : null;

  const circuit = (
    <CircuitBlock
      run={run}
      c={shown}
      agentIds={step?.nodeIds ?? []}
      big={big}
      note={walkVersion && walkVersion.id !== version?.id ? <span className="b-tag">The walkthrough is showing {walkVersion.label}</span> : null}
    />
  );

  const rail = (
    <ol className="b-rail" aria-label="Steps">
      {steps.map((s) => {
        const here = step?.stepId === s.id;
        const isOpen = open === s.id;
        return (
          <li key={s.id} data-off={s.off ? "" : undefined} data-agent={here ? "" : undefined} data-kind={s.kind}>
            <button type="button" className="b-rail-b" aria-expanded={isOpen} aria-controls={`rail-${s.id}`} onClick={() => setOpen(isOpen ? null : s.id)}>
              <span className="b-rail-t">{s.title}</span>
              <span className="b-st">
                <Sq kind={s.off ? "wait" : s.kind} /> {s.word}
              </span>
              {s.detail ? <span className="b-rail-d">{s.detail}</span> : null}
              {here ? <span className="b-tag b-rail-here">Walkthrough is here</span> : null}
            </button>
            {isOpen ? (
              <div className="b-rail-e" id={`rail-${s.id}`}>
                <Evidence id={s.id} run={run} c={shown} human={human} signoff={signoff} assumed={assumed} onPickVersion={onPickVersion} />
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );

  return (
    <div className="b-arch" data-big={big ? "" : undefined}>
      <div className="b-arch-bar">
        {picker}
        <button type="button" className="bx ghost sm" onClick={onExpand}>
          {big ? <Minimize2 aria-hidden className="ico" /> : <Maximize2 aria-hidden className="ico" />}
          {big ? "Back to chat" : "Expand"}
        </button>
      </div>
      {big ? (
        <div className="b-arch-grid">
          <div className="b-arch-side">
            {walkCard}
            {rail}
          </div>
          {circuit}
        </div>
      ) : (
        <>
          {walkCard}
          {circuit}
          {rail}
        </>
      )}
    </div>
  );
}
