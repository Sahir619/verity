// The only place numbers become text (ARCHITECTURE 12). Measured values render in font-mono at the call site.

/** Real minus sign (U+2212), not a hyphen. */
const MINUS = "−";

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}

function fixed(x: number, digits: number): string {
  const sign = x < 0 ? MINUS : "";
  return sign + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 1840 -> "1,840 µm²" */
export function formatArea(um2: number): string {
  return `${Math.round(um2).toLocaleString("en-US")} µm²`;
}

/** 3.38 -> "3.38 ns" */
export function formatNs(ns: number): string {
  return `${ns.toFixed(2)} ns`;
}

/** 1.66 -> "1.66 mW" */
export function formatMw(mw: number): string {
  return `${mw.toFixed(2)} mW`;
}

/** 295.86 -> "295.9 MHz" */
export function formatMhz(mhz: number): string {
  return `${fixed(round1(mhz), 1)} MHz`;
}

/** 17.4 -> "17.4%", -17.4 -> "−17.4%". Signed output is formatDelta's job. */
export function formatPct(x: number): string {
  return `${fixed(round1(x), 1)}%`;
}

export type DeltaDirection = "better" | "worse" | "neutral";

/**
 * Percent change of `value` against `baseline`, with a direction that respects which way is better.
 * Text is signed with a real minus ("−17.4%", "+5.6%"). Neutral when the rounded change is under 0.5 percent.
 */
export function formatDelta(
  value: number,
  baseline: number,
  opts: { lowerIsBetter: boolean },
): { text: string; direction: DeltaDirection } {
  if (baseline === 0) return { text: "n/a", direction: "neutral" };
  const delta = round1(((value - baseline) / baseline) * 100);
  const sign = delta > 0 ? "+" : delta < 0 ? MINUS : "";
  const text = `${sign}${Math.abs(delta).toFixed(1)}%`;
  if (Math.abs(delta) < 0.5) return { text, direction: "neutral" };
  const improved = opts.lowerIsBetter ? delta < 0 : delta > 0;
  return { text, direction: improved ? "better" : "worse" };
}

/** 0.18 -> "$0.18" */
export function formatUsd(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

/** 90000 -> "1:30" */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** (16, 16) -> "16/16" */
export function formatTests(passed: number, total: number): string {
  return `${passed}/${total}`;
}

/** CPU profile and estimate values, with missing measurements kept explicit. */
export function formatCpuNumber(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return "Not measured";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

/** Benchmark time in microseconds, in the largest unit that keeps it readable: 3422083.72 -> "3.422 s". */
export function formatMicros(us: number | null): string {
  if (us === null || !Number.isFinite(us)) return "Not measured";
  if (us >= 1e6) return `${formatCpuNumber(us / 1e6, 3)} s`;
  if (us >= 1e3) return `${formatCpuNumber(us / 1e3, 3)} ms`;
  return `${formatCpuNumber(us)} us`;
}
