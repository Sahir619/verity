"use client";

import type { Candidate } from "@/lib/contracts/types";
import { CodeView } from "@/components/domain/CodeView";
import { Sq } from "../studio/loop/LoopModule";

/** Where a version stands, as a plain phrase to follow its name. A run that has ended is not still working on anything. */
function stateOf(c: Candidate, isWinner: boolean, ended: boolean): { kind: "pass" | "fail" | "run" | "drop"; word: string } {
  if (isWinner) return { kind: "pass", word: "is the winner" };
  switch (c.status) {
    case "drafting":
      return ended ? { kind: "drop", word: "was not finished, the run ended" } : { kind: "run", word: "is being written" };
    case "linting":
    case "simulating":
      return ended ? { kind: "drop", word: "was not finished, the run ended" } : { kind: "run", word: "is being tested" };
    case "fixing":
      return ended ? { kind: "drop", word: "was not finished, the run ended" } : { kind: "run", word: "is being fixed" };
    case "synthesizing":
      return ended ? { kind: "drop", word: "was not finished, the run ended" } : { kind: "run", word: "is being measured" };
    case "fail":
      return { kind: "fail", word: "failed a test" };
    case "rejected":
      return { kind: "fail", word: "was dropped" };
    case "pass":
      return { kind: "pass", word: "passes the tests" };
    case "measured":
      return { kind: "pass", word: "passes the tests and was measured" };
  }
}

/**
 * The Verilog of one version, with why it was written. The parent picks the version (the one you chose, else the one the
 * run follows, else the best one when the run has ended). `ended`: no version is described as still being worked on.
 */
export function RtlModule({ candidate, isWinner, ended }: { candidate: Candidate | null; isWinner: boolean; ended: boolean }) {
  if (!candidate) {
    return <p className="b-empty">{ended ? "No version was written, so there is no Verilog." : "The Verilog of each version appears here as the agent writes it."}</p>;
  }
  const st = stateOf(candidate, isWinner, ended);
  const attempt = candidate.attempts.length;
  return (
    <div className="b-rtl">
      <p className="b-rtl-why">
        <Sq kind={st.kind} /> <b>{candidate.label}</b> {st.word}
        {attempt > 1 ? `, fixed by the agent (attempt ${attempt})` : ""}.{candidate.reason ? ` ${candidate.reason}` : ""}
      </p>
      {candidate.rtl ? (
        <CodeView code={candidate.rtl} language="verilog" filename={`${candidate.label}.v`} />
      ) : (
        <p className="b-empty">
          {ended
            ? `${candidate.label} has no Verilog. The run ended before it was written.`
            : `${candidate.label} is being written. Its Verilog appears here as soon as the agent writes it.`}
        </p>
      )}
    </div>
  );
}
