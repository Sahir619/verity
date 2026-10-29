import { formatDelta } from "@/lib/format";

/**
 * Signed one-decimal number ("+12.5", "−7.8") for fitness gains and scores. lib/format owns the real
 * minus sign (U+2212) inside formatDelta, so this borrows it: a gain of g is a change of g percent on 100.
 */
export function formatSigned(x: number): string {
  return formatDelta(100 + x, 100, { lowerIsBetter: false }).text.replace("%", "");
}

/** Unsigned-positive score ("6.8", "−0.7"). */
export function formatScore(x: number): string {
  const text = formatSigned(x);
  return text.startsWith("+") ? text.slice(1) : text;
}
