import type { Equivalence } from "@/lib/contracts/types";

export type ProofResult = { result: "proven" | "open"; label: string; sentence: string };

/** What an equivalence result may claim. "Proven" only when EQY says so; anything else stays an open claim. */
export function proofText(eqy: Equivalence, againstLabel: string): ProofResult {
  if (eqy.status === "proven") {
    return { result: "proven", label: "PROVEN EQUAL (EQY)", sentence: `Proven equal to ${againstLabel} by Yosys EQY.` };
  }
  if (eqy.status === "not_equivalent") {
    return { result: "open", label: "NOT EQUIVALENT (EQY)", sentence: `Yosys EQY found a difference from ${againstLabel}.` };
  }
  return {
    result: "open",
    label: "TESTED, NOT PROVEN",
    sentence:
      eqy.status === "inconclusive"
        ? `Passed the locked tests. Yosys EQY could not prove it equal to ${againstLabel}.`
        : `Passed the locked tests. Equivalence with ${againstLabel} was not checked.`,
  };
}
