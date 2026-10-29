"use client";

import { useMemo, type ReactNode } from "react";
import type { Equivalence, PPA, Priority, RunEvent, RunState } from "@/lib/contracts/types";
import { formatArea, formatDelta, formatMw, formatNs } from "@/lib/format";
import { useVerityStore } from "@/lib/store";
import { formatFitness } from "../shared/fitness-text";
import { Sq } from "../studio/loop/LoopModule";
import { GOAL_WORD, lostOnScore } from "./chat";
import { TERMINAL, discardWhy, dropNotesOf, scoreRows, type ScoreRow } from "./derive";

function fmt(key: ScoreRow["key"], v: number | null): string {
  if (v === null) return "not measured";
  return key === "area" ? formatArea(v) : key === "clock" ? formatNs(v) : formatMw(v);
}

/** "−1.6% vs v1" from two measured values; null when either is missing. Lower is better for all three. */
function vsBase(value: number | null, base: number | null | undefined, baseLabel: string): string | null {
  if (value === null || !base) return null;
  return `${formatDelta(value, base, { lowerIsBetter: true }).text} vs ${baseLabel}`;
}

function proofWords(status: Equivalence["status"], against: string): string {
  if (status === "proven") return `Proven to work the same as ${against}`;
  if (status === "not_equivalent") return `The proof found a difference from ${against}`;
  if (status === "inconclusive") return `Passes every test, but not proven equal to ${against}`;
  return `Passes every test. Not checked against ${against}`;
}

function Scoreboard({ winner, human, winnerLabel }: { winner: PPA; human: { ppa: PPA; eqy: Equivalence }; winnerLabel: string }) {
  const rows = scoreRows(winner, human.ppa);
  const eqy = human.eqy.status;
  return (
    <section className="b-sec" aria-label="Compared with a human-written design">
      <h3 className="b-sec-h">Compared with a human-written design</h3>
      <table className="b-score">
        <thead>
          <tr>
            <th scope="col">Measure</th>
            <th scope="col">Verity {winnerLabel}</th>
            <th scope="col">Human</th>
            <th scope="col">Verity vs human</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th scope="row">{r.label}</th>
              <td className="m">{fmt(r.key, r.verity)}</td>
              <td className="m">{fmt(r.key, r.human)}</td>
              <td className="m">
                {r.delta ? `${r.delta.text} ${r.delta.direction === "better" ? "(Verity better)" : r.delta.direction === "worse" ? "(human better)" : "(even)"}` : "n/a"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="b-note">
        <Sq kind={eqy === "proven" ? "pass" : "fail"} />{" "}
        {eqy === "proven"
          ? "Proven to work the same as the human design."
          : eqy === "not_equivalent"
            ? "Does not work the same as the human design."
            : eqy === "inconclusive"
              ? "Tested against the human design, but not proven the same."
              : "Not checked against the human design."}
      </p>
      <details className="b-more">
        <summary>About the human design</summary>
        <p>
          It is the hand-written reference.v that ships with this chip, built at the same settings and measured with the same tools
          {human.ppa.tools.length ? ` (${human.ppa.tools.join(", ")}, ${human.ppa.pdk})` : ""}. It is shown even when the human design wins. The
          proof uses Yosys EQY.
        </p>
      </details>
    </section>
  );
}

/**
 * The Result tab: the winner with its numbers against our own v1, how it was picked, the comparison with a human
 * design, and every dropped version with the reason from its own run record. Default text stays plain; the detail and
 * the tool names live in the dropdowns. Nothing here is computed beyond reading and formatting run data.
 */
export function ResultModule({
  run,
  hasRun,
  human,
  humanFeature,
  priority,
  signoff,
  events,
}: {
  run: RunState;
  hasRun: boolean;
  human: { ppa: PPA; eqy: Equivalence } | null;
  humanFeature: boolean;
  priority: Priority;
  /** The sign-off sheet in one line, under the winner. Nothing from a server without sign-off checks. */
  signoff?: ReactNode;
  /** The events of a run that is not the page's own (the CPU panel's run view). Default: the page run's log in the store. */
  events?: RunEvent[];
}) {
  const storeLog = useVerityStore((s) => s.log);
  const log = events ?? storeLog;
  const dropNotes = useMemo(() => (hasRun ? dropNotesOf(log) : new Map<string, string>()), [hasRun, log]);
  const finished = hasRun && TERMINAL.has(run.phase);
  const picked = run.winnerId ? run.candidates[run.winnerId] : null;
  const base = run.baselineId ? run.candidates[run.baselineId] : null;
  // A run that ended before a winner was picked (stopped, cap, error) still shows v1 as "Best so far", like the chat says.
  const winner = picked ?? (finished ? base : null);
  const baseLabel = base?.label ?? "v1";
  const discarded = run.candidateOrder
    .map((id) => run.candidates[id])
    .filter((c) => c !== undefined)
    .map((c) => ({ c, why: discardWhy(c, run, dropNotes) }))
    .filter((d): d is { c: (typeof d)["c"]; why: string } => d.why !== null);
  const lost = lostOnScore(run, priority);
  const weights = picked?.fitness?.weights;
  const tools = winner?.ppa ? `${winner.ppa.tools.join(", ") || "open tools"} on ${winner.ppa.pdk}` : null;

  const row = (label: string, value: number | null, baseValue: number | null | undefined, text: (v: number) => string) => (
    <div>
      <dt>{label}</dt>
      <dd>
        <span className="m">{value !== null ? text(value) : "not measured"}</span>
        {winner?.id === base?.id ? null : <small className="m">{vsBase(value, baseValue, baseLabel) ?? ""}</small>}
      </dd>
    </div>
  );

  return (
    <div className="b-result">
      {!hasRun ? (
        <p className="b-empty">The winner and its numbers appear here when a run ends.</p>
      ) : !winner ? (
        <p className="b-empty">{finished ? "This run ended without a measured winner." : "No winner yet. Versions are still being built and tested."}</p>
      ) : (
        <section className="b-sec" aria-label="Winner">
          <p className="b-lab">{finished && picked ? "Winner" : "Best so far"}</p>
          <p className="b-win">{winner.label}</p>
          {winner.eqy ? (
            <p className="b-proof">
              <Sq kind={winner.eqy.status === "proven" ? "pass" : "fail"} /> {proofWords(winner.eqy.status, baseLabel)}
            </p>
          ) : null}
          {winner.ppa ? (
            <dl className="b-rows">
              {row("Area", winner.ppa.areaUm2, base?.ppa?.areaUm2, formatArea)}
              {row("Clock period", winner.ppa.clockPeriodNs, base?.ppa?.clockPeriodNs, formatNs)}
              {row("Power (estimate)", winner.ppa.powerMw, base?.ppa?.powerMw, formatMw)}
              <div>
                <dt>Versions tried</dt>
                <dd className="m">{run.candidateOrder.length}</dd>
              </div>
            </dl>
          ) : (
            <p className="b-note">Not measured yet.</p>
          )}
          {!picked ? (
            <p className="b-note">{baseLabel} is the first version that passed the tests. The run ended before a winner was picked.</p>
          ) : winner.id === base?.id ? (
            <p className="b-note">{base.label} is the first version that passed. No later version beat it.</p>
          ) : (
            <p className="b-note b-dim">Lower is better for all three. Every change is against our own {baseLabel}.</p>
          )}
          {signoff}
        </section>
      )}

      {winner ? (
        <section className="b-sec b-explain" aria-label="Explanations">
          {picked ? (
            <>
              <details className="b-more">
                <summary>How the winner is picked</summary>
                <p>
                  Every version that passes all the tests is measured for size (area), speed (clock period) and power. Each one gets a combined
                  score: how much better it is than {baseLabel} on each, weighted by your goal. The best score wins.
                </p>
                {weights ? (
                  <p>
                    Your goal was {GOAL_WORD[priority]}, so area counted {Math.round(weights.area * 100)}%, clock period{" "}
                    {Math.round(weights.timing * 100)}% and power {Math.round(weights.power * 100)}%.
                    {picked.fitness && picked.id !== base?.id ? ` ${picked.label} scored ${formatFitness(picked.fitness.score)}.` : null}
                  </p>
                ) : null}
                {lost.map((s) => (
                  <p key={s}>{s}</p>
                ))}
              </details>
              <details className="b-more">
                <summary>What does proven equal mean</summary>
                <p>
                  Tests check the cases someone wrote down. After the winner is picked, a formal checker (Yosys EQY) compares it with {baseLabel} for
                  every possible input. Proven equal means it found no input where the two give different answers. When the proof cannot finish,
                  Verity says the winner passes every test but is not proven.
                </p>
              </details>
            </>
          ) : null}
          {tools ? (
            <details className="b-more">
              <summary>How these numbers were measured</summary>
              <p>
                Measured by {tools}. These are open tools, not a chip factory&apos;s signoff tools, so the numbers are best read against{" "}
                {baseLabel}, which was measured the same way. Power is an estimate.
              </p>
            </details>
          ) : null}
        </section>
      ) : null}

      {hasRun && picked?.ppa && human ? (
        <Scoreboard winner={picked.ppa} human={human} winnerLabel={picked.label} />
      ) : hasRun && picked ? (
        <section className="b-sec" aria-label="Compared with a human-written design">
          <h3 className="b-sec-h">Compared with a human-written design</h3>
          <p className="b-note">
            {!humanFeature
              ? "This server does not compare with a human-written design yet."
              : finished
                ? "No human-written design was measured for this run."
                : "Measured against a human-written design after the winner is picked."}
          </p>
        </section>
      ) : null}

      {discarded.length > 0 ? (
        <details className="b-more b-sec">
          <summary>
            Why the other versions were dropped ({discarded.length})
          </summary>
          <ul className="b-disc">
            {discarded.map(({ c, why }) => (
              <li key={c.id}>
                <span className="b-disc-v">
                  <Sq kind={c.status === "rejected" ? "fail" : "drop"} />
                  {c.label}
                </span>
                <span>{why}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
