"use client";

import type { RunState } from "@/lib/contracts/types";
import { Sq } from "../studio/loop/LoopModule";
import { signoffOf, type Signoff } from "./artifacts";
import { TERMINAL } from "./derive";

/**
 * Where the winner's sign-off sheet stands. "unsupported": this server does not run sign-off checks (no "signoff" in
 * /health), so nothing is said. "waiting": the run has not picked its winner yet. "running": the run is done and its
 * stream is still open, so the sheet can still come. "none": the run ended without one.
 */
export type SignoffView =
  | { state: "ok"; sheet: Signoff; label: string }
  | { state: "bad" | "waiting" | "running" | "none" | "unsupported" };

export function signoffView(run: RunState, opts: { feature: boolean; tailOpen: boolean }): SignoffView {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  if (winner) {
    const r = signoffOf(run, winner.id);
    if (r.state === "ok") return { state: "ok", sheet: r.value, label: winner.label };
    if (r.state === "bad") return { state: "bad" };
  }
  if (!opts.feature) return { state: "unsupported" };
  if (!TERMINAL.has(run.phase)) return { state: "waiting" };
  return run.phase === "done" && winner && opts.tailOpen ? { state: "running" } : { state: "none" };
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The tests per seed, from the sheet. Every seed runs the same locked tests. */
function testsPerSeed(s: Signoff): number | null {
  const totals = new Set(s.tests.perSeed.map((p) => p.total));
  return totals.size === 1 ? [...totals][0] : null;
}

function testsWords(s: Signoff): string {
  const per = testsPerSeed(s);
  const { seedsRun, seedsPassed } = s.tests;
  if (seedsRun === 0) return "tests not run on extra seeds";
  if (per !== null && seedsPassed === seedsRun) return `${per} of ${per} tests on ${plural(seedsRun, "seed", "seeds")}`;
  return `${seedsPassed} of ${plural(seedsRun, "seed", "seeds")} passed every test`;
}

function lintWords(s: Signoff): string {
  return `lint ${plural(s.lint.errors, "error", "errors")} ${plural(s.lint.warnings, "warning", "warnings")}`;
}

type Proof = Signoff["eqyVsHuman"];

function proofPhrase(status: Proof, against: string): string {
  if (status === "proven") return `proven equal to ${against}`;
  if (status === "not_equivalent") return `different from ${against}`;
  if (status === "inconclusive") return `not proven equal to ${against}`;
  return `not checked against ${against}`;
}

function proofsWords(s: Signoff, baseLabel: string): string {
  const human = "the human design";
  if (s.eqyVsV1 === "v1_is_winner") return `${proofPhrase(s.eqyVsHuman, human)} (${baseLabel} is the winner)`;
  if (s.eqyVsV1 === "proven" && s.eqyVsHuman === "proven") return `proven equal to ${baseLabel} and to ${human}`;
  return `${proofPhrase(s.eqyVsV1, baseLabel)}, ${proofPhrase(s.eqyVsHuman, human)}`;
}

function strengthWords(s: Signoff): string | null {
  const st = s.strength;
  if (!st) return null;
  if (st.status === "skipped") return "test strength not checked";
  if (st.status === "failed") return "the test strength check did not finish";
  return `tests caught ${st.caught} of ${plural(st.planted, "planted bug", "planted bugs")}`;
}

/** The one line on the Result tab: "Sign-off: 16 of 16 tests on 9 seeds, lint 0 errors 2 warnings, ...". From the sheet only. */
export function signoffLine(s: Signoff, baseLabel: string): string {
  const parts = [testsWords(s), lintWords(s), proofsWords(s, baseLabel), strengthWords(s)].filter((x): x is string => x !== null);
  return `Sign-off: ${parts.join(", ")}.`;
}

/** Whether every check on the sheet came out clean (the tests on every seed, no lint errors or latches, the proofs that ran). */
export function signoffClean(s: Signoff): boolean {
  const proofOk = (p: string) => p === "proven" || p === "v1_is_winner" || p === "not_run";
  return s.tests.seedsPassed === s.tests.seedsRun && s.lint.errors === 0 && s.lint.latches === 0 && proofOk(s.eqyVsV1) && proofOk(s.eqyVsHuman);
}

type Kind = "pass" | "fail" | "wait" | "drop";

function proofRow(status: Signoff["eqyVsV1"], against: string): { kind: Kind; word: string } {
  switch (status) {
    case "proven":
      return { kind: "pass", word: "Proven (Yosys EQY)" };
    case "not_equivalent":
      return { kind: "fail", word: `Differs from ${against} (Yosys EQY found an input where they disagree)` };
    case "inconclusive":
      return { kind: "fail", word: "Not proven: the proof did not finish" };
    case "v1_is_winner":
      return { kind: "drop", word: "Not needed: this is v1" };
    case "not_run":
      return { kind: "wait", word: "Not checked" };
  }
}

function seconds(ms: number): string {
  return ms >= 1000 ? `${Math.round(ms / 1000)} s` : `${ms} ms`;
}

/**
 * The sign-off sheet of the winner: the checks an engineer looks at before handing a block on, each a shape and a word.
 * Every value is read from signoff/<id>.json; nothing is filled in.
 */
export function SignoffSheet({ sheet, label, baseLabel }: { sheet: Signoff; label: string; baseLabel: string }) {
  const per = testsPerSeed(sheet);
  const tags = Object.entries(sheet.lint.byTag).sort((a, b) => b[1] - a[1]);
  const st = sheet.strength;
  const rows: { name: string; kind: Kind; word: string }[] = [
    {
      name: "Tests",
      kind: sheet.tests.seedsRun > 0 && sheet.tests.seedsPassed === sheet.tests.seedsRun ? "pass" : sheet.tests.seedsRun === 0 ? "wait" : "fail",
      word:
        sheet.tests.seedsRun === 0
          ? "Not run on extra seeds"
          : per !== null && sheet.tests.seedsPassed === sheet.tests.seedsRun
            ? `${per} of ${per}, on ${plural(sheet.tests.seedsRun, "seed", "seeds")}`
            : `${sheet.tests.seedsPassed} of ${plural(sheet.tests.seedsRun, "seed", "seeds")} passed every test`,
    },
    {
      name: "Lint",
      kind: sheet.lint.errors === 0 ? "pass" : "fail",
      word: `${plural(sheet.lint.errors, "error", "errors")}, ${plural(sheet.lint.warnings, "warning", "warnings")}${tags.length ? ` (${tags.map(([t]) => t).join(", ")})` : ""}`,
    },
    {
      name: "Latches",
      kind: sheet.lint.latches === 0 ? "pass" : "fail",
      word: sheet.lint.latches === 0 ? "None (Verilator latch check)" : `${plural(sheet.lint.latches, "latch", "latches")} found (Verilator latch check)`,
    },
    { name: `Equal to ${baseLabel}`, ...proofRow(sheet.eqyVsV1, baseLabel) },
    { name: "Equal to the human design", ...proofRow(sheet.eqyVsHuman, "the human design") },
  ];
  if (st) {
    rows.push({
      name: "Test strength",
      kind: st.status === "ok" ? (st.caught + st.harmless === st.planted ? "pass" : "fail") : st.status === "skipped" ? "drop" : "fail",
      word:
        st.status === "ok"
          ? `Caught ${st.caught} of ${plural(st.planted, "planted bug", "planted bugs")}${st.harmless ? `; ${st.harmless} proven harmless` : ""}${st.undecided ? `; ${st.undecided} undecided` : ""}`
          : st.status === "skipped"
            ? "Not checked"
            : "The check did not finish",
    });
  }
  return (
    <section className="b-so" aria-label={`Sign-off sheet for ${label}`}>
      <p className="b-so-h">
        Sign-off <span aria-hidden>·</span> {label}
      </p>
      <dl className="b-so-rows">
        {rows.map((r) => (
          <div key={r.name}>
            <dt>{r.name}</dt>
            <dd>
              <Sq kind={r.kind} /> {r.word}
            </dd>
          </div>
        ))}
      </dl>
      {st?.note ? <p className="b-note b-dim">{st.note}</p> : null}
      <details className="b-more">
        <summary>The detail behind the sheet</summary>
        {sheet.tests.perSeed.length > 0 ? (
          <p>
            Seeds: {sheet.tests.perSeed.map((p) => `seed ${p.seed}, ${p.passed} of ${p.total}`).join("; ")}.
          </p>
        ) : null}
        {tags.length > 0 ? <p>Lint warnings by tag: {tags.map(([t, n]) => `${t} ${n}`).join(", ")}. None are hidden.</p> : <p>No lint warnings.</p>}
        {st?.status === "ok" ? (
          <p>
            Test strength: Verity planted {plural(st.planted, "small bug", "small bugs")} in a copy of the design and ran the locked tests on each. A bug the
            tests did not catch is either proven harmless (it changes nothing a test can see) or left undecided.
          </p>
        ) : null}
        <p>
          These checks ran in {seconds(sheet.durationMs)} after the result showed. They use open tools (Verilator, iverilog and cocotb, Yosys EQY), not
          a chip factory&apos;s sign-off tools.
        </p>
      </details>
    </section>
  );
}

/** The sheet, or what stands in for it while it is not there. Nothing at all on a server without sign-off checks. */
export function SignoffBlock({ view, baseLabel }: { view: SignoffView; baseLabel: string }) {
  switch (view.state) {
    case "ok":
      return <SignoffSheet sheet={view.sheet} label={view.label} baseLabel={baseLabel} />;
    case "running":
      return (
        <p className="b-note" role="status">
          <Sq kind="run" /> Sign-off checks running
        </p>
      );
    case "waiting":
      return <p className="b-empty">The sign-off checks run on the winner, after the result shows.</p>;
    case "bad":
      return <p className="b-empty">The sign-off sheet came, but it could not be read.</p>;
    case "none":
      return <p className="b-empty">No sign-off sheet came for this run.</p>;
    case "unsupported":
      return <p className="b-empty">This server does not run sign-off checks yet.</p>;
  }
}
