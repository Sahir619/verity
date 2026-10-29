"use client";

import { memo, useCallback } from "react";
import type { Candidate, PPA, RunState } from "@/lib/contracts/types";
import type { Selection } from "@/lib/contracts/view-types";
import { formatArea, formatDelta, formatNs } from "@/lib/format";
import { useUi } from "@/lib/store";
import { Mod } from "../chrome/Mod";
import { rejectedText } from "../figure/derive";
import { proofText } from "../figure/proof";
import { Sq } from "../loop/LoopModule";

type Kind = "pass" | "fail" | "run" | "wait" | "drop";

/** The status shape and word of a version. Never colour alone: a square plus a word. */
function statusOf(c: Candidate, isWinner: boolean, settled: boolean): { kind: Kind; word: string; accent: boolean } {
  if (isWinner) return { kind: "pass", word: "Winner", accent: true };
  switch (c.status) {
    case "drafting":
      return { kind: "run", word: "Writing", accent: false };
    case "linting":
      return { kind: "run", word: "Linting", accent: false };
    case "simulating":
      return { kind: "run", word: "Simulating", accent: false };
    case "fail":
      return { kind: "fail", word: "Fail", accent: false };
    case "fixing":
      return { kind: "fail", word: "Fixing", accent: false };
    case "pass":
      return { kind: "pass", word: "Pass", accent: false };
    case "synthesizing":
      return { kind: "run", word: "Measuring", accent: false };
    case "measured":
      return c.isBaseline ? { kind: "pass", word: "Baseline", accent: false } : c.isElite ? { kind: "pass", word: "Kept", accent: false } : settled ? { kind: "drop", word: "Dropped", accent: false } : { kind: "pass", word: "Measured", accent: false };
    case "rejected":
      return { kind: "fail", word: "Rejected", accent: false };
  }
}

/** Why a row has no measured numbers yet. Never a number that is not in the record. */
function unmeasured(c: Candidate): string {
  if (c.status === "rejected") return `Never measured: ${rejectedText(c).toLowerCase()}`;
  if (c.status === "synthesizing") return "Measuring";
  if (c.status === "fail" || c.status === "fixing") return "Not measured: fails the locked tests";
  return "Not measured yet";
}

function Delta({ value, base, label }: { value: number | null; base: number | null | undefined; label: string }) {
  if (value === null || !base) return null;
  const d = formatDelta(value, base, { lowerIsBetter: true });
  return (
    <small className="t1-d" data-dir={d.direction}>
      {d.text}
      <span className="sr">{d.direction === "better" ? ` lower ${label}` : d.direction === "worse" ? ` higher ${label}` : ` about the same ${label}`}</span>
    </small>
  );
}

type RowProps = {
  c: Candidate;
  base: PPA | null;
  isWinner: boolean;
  settled: boolean;
  pinned: boolean;
  followed: boolean;
  onPick(id: string): void;
};

const Row = memo(function Row({ c, base, isWinner, settled, pinned, followed, onPick }: RowProps) {
  const st = statusOf(c, isWinner, settled);
  const area = c.ppa?.areaUm2 ?? null;
  const clk = c.ppa?.clockPeriodNs ?? null;
  const pwr = c.ppa?.powerMw ?? null;
  const measured = c.ppa !== null && (area !== null || clk !== null || pwr !== null);
  const rel = !c.isBaseline;

  return (
    <tr
      data-on={pinned ? "" : undefined}
      data-follow={followed && !pinned ? "" : undefined}
      data-rejected={c.status === "rejected" ? "" : undefined}
      data-winner={isWinner ? "" : undefined}
      data-live={st.kind === "run" ? "" : undefined}
      onClick={() => onPick(c.id)}
    >
      <th scope="row">
        <button type="button" className="t1-ver" aria-pressed={pinned} aria-label={`${c.label}, show details`}>
          {c.label}
        </button>
        <small className="t1-by">{c.createdBy}</small>
      </th>
      {measured ? (
        <>
          <td className="num m">{area !== null ? formatArea(area) : <span className="t1-na">n/a</span>}</td>
          <td className="num m">
            {c.isBaseline ? <span className="t1-na">baseline</span> : rel && area !== null ? <Delta value={area} base={base?.areaUm2} label="area" /> : <span className="t1-na">n/a</span>}
          </td>
          <td className="num m">
            {clk !== null ? formatNs(clk) : <span className="t1-na">n/a</span>}
            {rel ? <Delta value={clk} base={base?.clockPeriodNs} label="clock period" /> : null}
          </td>
          <td className="num m">
            {pwr !== null ? pwr.toFixed(2) : <span className="t1-na">n/a</span>}
            {rel ? <Delta value={pwr} base={base?.powerMw} label="power" /> : null}
          </td>
        </>
      ) : (
        <td colSpan={4} className="t1-wait">
          {unmeasured(c)}
        </td>
      )}
      <td>
        <span className="st">
          <Sq kind={st.kind} />
          <span className="word" data-accent={st.accent ? "" : undefined}>
            {st.word}
          </span>
        </span>
      </td>
    </tr>
  );
});

/** Winner against the baseline in plain words, from the record only. */
function versionsNote(run: RunState, baselineLabel: string, base: PPA | null): string {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  if (winner?.ppa && base && winner.id !== run.baselineId) {
    const bits: string[] = [];
    const a = winner.ppa.areaUm2;
    const c = winner.ppa.clockPeriodNs;
    const p = winner.ppa.powerMw;
    if (a !== null && base.areaUm2) bits.push(`area ${formatDelta(a, base.areaUm2, { lowerIsBetter: true }).text}`);
    if (c !== null && base.clockPeriodNs) {
      const d = formatDelta(c, base.clockPeriodNs, { lowerIsBetter: true });
      bits.push(`clock period ${d.text}${d.direction === "worse" ? " (slower)" : d.direction === "better" ? " (faster)" : ""}`);
    }
    if (p !== null && base.powerMw) bits.push(`power ${formatDelta(p, base.powerMw, { lowerIsBetter: true }).text}`);
    if (bits.length > 0) return `${winner.label}: ${bits.join(", ")} vs ${baselineLabel}.`;
  }
  if (base) return `${baselineLabel} is the baseline. Every figure here is compared to it.`;
  return "Versions appear as the agent writes them.";
}

/** The equivalence lamp: waiting, checking, or the EQY result. "Proven" only when the record says proven. */
function EqyLamp({ run, baselineLabel }: { run: RunState; baselineLabel: string }) {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  let kind: Kind = "wait";
  let word = "EQY waiting";
  let note = "after the winner is picked";
  let accent = false;
  if (winner?.eqy) {
    const p = proofText(winner.eqy, baselineLabel);
    kind = p.result === "proven" ? "pass" : "fail";
    accent = p.result === "proven";
    word = p.label;
    note = `${winner.label} vs ${baselineLabel}`;
  } else if (run.stages.prove.status === "active") {
    kind = "run";
    word = "EQY checking";
    note = winner ? `${winner.label} vs ${baselineLabel}` : "";
  }
  return (
    <div className="eqy-s" data-state={kind}>
      <Sq kind={kind} />
      <span className="word" data-accent={accent ? "" : undefined}>
        {word}
      </span>
      {note ? <span className="lab eqy-n">{note}</span> : null}
    </div>
  );
}

/**
 * The VERSIONS module: one row per version, filled in only as the record measures it, then the winner note and the
 * EQY lamp. Clicking a row pins the inspector to that version (ui.select), or does what `onPick` says instead.
 */
export function CharacteristicsTable({ run, follow, onPick }: { run: RunState; follow: Selection | null; onPick?(id: string): void }) {
  const { selection, select } = useUi();
  const order = run.candidateOrder.map((id) => run.candidates[id]).filter((c): c is Candidate => Boolean(c));
  const baselineId = run.baselineId ?? order.find((c) => c.isBaseline)?.id ?? null;
  const baseline = baselineId ? run.candidates[baselineId] : undefined;
  const baselineLabel = baseline?.label ?? "v1";
  const toolsPpa = baseline?.ppa ?? order.find((c) => c.ppa)?.ppa ?? null;
  const tools = toolsPpa ? [...toolsPpa.tools, toolsPpa.pdk].filter(Boolean).join(", ") : null;
  const pick = useCallback((id: string) => (onPick ? onPick(id) : select({ kind: "candidate", id })), [onPick, select]);

  return (
    <Mod label="Versions" right={<span className="lab">All deltas vs {baselineLabel}</span>} ariaLabel="Versions">
      <div className="t1">
        <div className="t1-scroll">
          <table className="dtbl t1-tbl" aria-label={`Versions, relative to ${baselineLabel}${tools ? `, ${tools}` : ""}. Measured values only.${run.sample ? " Sample data." : ""}`}>
            <thead>
              <tr>
                <th scope="col">VER</th>
                <th scope="col" className="num">
                  AREA µm²
                </th>
                <th scope="col" className="num">
                  AREA VS {baselineLabel}
                </th>
                <th scope="col" className="num">
                  CLOCK ns
                </th>
                <th scope="col" className="num">
                  POWER mW
                </th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {order.length === 0 ? (
                <tr>
                  <td colSpan={6} className="t1-wait">
                    No versions yet. v1 is written after the golden model is locked.
                  </td>
                </tr>
              ) : (
                order.map((c) => (
                  <Row
                    key={c.id}
                    c={c}
                    base={baseline?.ppa ?? null}
                    isWinner={c.id === run.winnerId}
                    settled={c.round < run.round || run.stages.evolve.status === "done"}
                    pinned={selection?.kind === "candidate" && selection.id === c.id}
                    followed={follow?.kind === "candidate" && follow.id === c.id}
                    onPick={pick}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="vnote">
          {versionsNote(run, baselineLabel, baseline?.ppa ?? null)}
          {tools ? <span className="vtools"> {tools}.</span> : null}
          {run.sample ? <span className="vtools"> Sample data.</span> : null}
        </p>
        <EqyLamp run={run} baselineLabel={baselineLabel} />
      </div>
    </Mod>
  );
}
