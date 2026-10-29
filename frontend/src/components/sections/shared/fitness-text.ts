// Fitness scores are not covered by lib/format.ts, so the one place they become text lives here.
// Uses the real minus sign (U+2212) like formatDelta.
export function formatFitness(score: number): string {
  const r = Math.round(score * 10) / 10;
  return `${r < 0 ? "−" : ""}${Math.abs(r).toFixed(1)}`;
}
