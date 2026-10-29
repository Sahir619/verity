import type { Candidate, Fitness, PPA, Priority, RunLog } from "@/lib/contracts/types";

const WEIGHTS: Record<Priority, { area: number; timing: number; power: number }> = {
  area: { area: 0.6, timing: 0.25, power: 0.15 },
  speed: { area: 0.25, timing: 0.6, power: 0.15 },
  power: { area: 0.25, timing: 0.15, power: 0.6 },
};

/**
 * ARCHITECTURE 8.3. Dev-only assertion helper: the agent computes fitness, the UI never decides with this.
 * A missing metric is dropped and the remaining weights are renormalized.
 */
export function recomputeFitness(baseline: PPA, candidate: PPA, priority: Priority): number {
  const w = WEIGHTS[priority];
  const parts: { w: number; gain: number }[] = [];
  const add = (weight: number, b: number | null, c: number | null) => {
    if (b === null || c === null) return;
    parts.push({ w: weight, gain: (100 * (b - c)) / b });
  };
  add(w.area, baseline.areaUm2, candidate.areaUm2);
  add(w.timing, baseline.clockPeriodNs, candidate.clockPeriodNs);
  add(w.power, baseline.powerMw, candidate.powerMw);
  const total = parts.reduce((s, p) => s + p.w, 0);
  if (total === 0) return 0;
  return parts.reduce((s, p) => s + (p.w / total) * p.gain, 0);
}

// ---------- display helpers (never used to decide anything) ----------

export type BreakdownRow = {
  metric: "area" | "timing" | "power";
  label: string;
  weight: number;
  /** Percent, positive = better than the baseline. Null when a measurement is missing. */
  gain: number | null;
  /** weight x gain, or null when the gain is missing. */
  contribution: number | null;
};

const METRIC_LABELS = { area: "area", timing: "timing", power: "power" } as const;

/** Rows for the "Fitness 6.8 = 0.60 x area +12.5 + ..." line. Weights come from the recorded Fitness. */
export function breakdownRows(fitness: Fitness): BreakdownRow[] {
  return (["area", "timing", "power"] as const).map((metric) => {
    const gain = fitness.gains[metric];
    const weight = fitness.weights[metric];
    return {
      metric,
      label: METRIC_LABELS[metric],
      weight,
      gain,
      contribution: gain === null ? null : weight * gain,
    };
  });
}

/** The PPA metric the run's priority is about, for the one-metric delta on a version card. */
export function priorityMetric(priority: Priority): { key: "area" | "clockPeriod" | "power"; label: string } {
  switch (priority) {
    case "area":
      return { key: "area", label: "Area" };
    case "speed":
      return { key: "clockPeriod", label: "Clock period" };
    case "power":
      return { key: "power", label: "Power" };
  }
}

/**
 * Dev-only assertion: stored fitness scores in a recording must match ARCHITECTURE 8.3 within 0.01.
 * Logs a warning per mismatch. Never used to decide anything.
 */
export function devCheckFitness(log: RunLog): void {
  const spec = log.events.find((e) => e.t === "spec");
  const priority: Priority = spec && spec.t === "spec" ? spec.spec.priority : "area";
  const finals = new Map<string, Candidate>();
  for (const e of log.events) if (e.t === "candidate") finals.set(e.candidate.id, e.candidate);
  const baseline = [...finals.values()].find((c) => c.isBaseline && c.ppa);
  if (!baseline || !baseline.ppa) return;
  for (const c of finals.values()) {
    if (!c.fitness || !c.ppa || c.isBaseline) continue;
    const expected = recomputeFitness(baseline.ppa, c.ppa, priority);
    if (Math.abs(expected - c.fitness.score) > 0.01) {
      console.warn(`[verity] fitness mismatch for ${c.label}: stored ${c.fitness.score}, recomputed ${expected.toFixed(2)}`);
    }
  }
}
