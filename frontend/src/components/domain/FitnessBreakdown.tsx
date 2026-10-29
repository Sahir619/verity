import type { CandidateStatus, Fitness } from "@/lib/contracts/types";
import { formatScore, formatSigned } from "./signed";

export type FitnessBreakdownProps = { fitness: Fitness | null; status: CandidateStatus };

function Term({ weight, name, gain }: { weight: number; name: string; gain: number | null }) {
  return (
    <span className="whitespace-nowrap">
      {weight.toFixed(2)} x {name} {gain === null ? "n/a" : formatSigned(gain)}
    </span>
  );
}

/** Shows how a fitness score is built from the weighted gains, with the numbers filled in (datasheet style). */
export function FitnessBreakdown({ fitness, status }: FitnessBreakdownProps) {
  if (!fitness) {
    const baseline = status === "measured";
    return (
      <p className="dp-empty">
        {baseline
          ? "Baseline. Every gain and fitness score is measured against this version."
          : "Not ranked. Only versions that pass the locked tests and are measured get a fitness score."}
      </p>
    );
  }
  const { weights, gains } = fitness;
  return (
    <div className="fitbox">
      <p className="fitbox-t">
        Fitness <span className="mono">{formatScore(fitness.score)}</span>
      </p>
      <p className="fitbox-eq mono">
        <span>=</span>
        <Term weight={weights.area} name="area" gain={gains.area} />
        <span>+</span>
        <Term weight={weights.timing} name="timing" gain={gains.timing} />
        <span>+</span>
        <Term weight={weights.power} name="power" gain={gains.power} />
      </p>
      <p className="fitbox-n">Gains are percent better than v1. Positive is better.</p>
    </div>
  );
}
