import type { ReactNode } from "react";

const SEG: Record<string, [number, number, number, number]> = {
  a: [2.4, 0, 7.2, 2.4],
  b: [9.6, 2.4, 2.4, 7.4],
  c: [9.6, 12.2, 2.4, 7.4],
  d: [2.4, 19.6, 7.2, 2.4],
  e: [0, 12.2, 2.4, 7.4],
  f: [0, 2.4, 2.4, 7.4],
  g: [2.4, 9.8, 7.2, 2.4],
};
const DIGITS = ["abcdef", "bc", "abdeg", "abcdg", "bcfg", "acdfg", "acdefg", "abc", "abcdefg", "abcdfg"];

/** A seven-segment readout for digits and colons. Decorative: the label carries the value. */
export function Seg7({ text, label }: { text: string; label: string }) {
  let x = 0;
  const rects: ReactNode[] = [];
  [...text].forEach((ch, i) => {
    if (ch === ":") {
      rects.push(<rect key={`${i}a`} x={x + 2} y={6} width={2.4} height={2.4} className="s7-on" />);
      rects.push(<rect key={`${i}b`} x={x + 2} y={14} width={2.4} height={2.4} className="s7-on" />);
      x += 7;
      return;
    }
    const on = DIGITS[Number(ch)] ?? "";
    for (const k of Object.keys(SEG)) {
      const [rx, ry, rw, rh] = SEG[k];
      rects.push(<rect key={`${i}${k}`} x={x + rx} y={ry} width={rw} height={rh} className={on.includes(k) ? "s7-on" : "s7-off"} />);
    }
    x += 16;
  });
  return (
    <svg className="s7" viewBox={`0 0 ${x} 22`} style={{ width: (x * 26) / 22 }} role="img" aria-label={label}>
      {rects}
    </svg>
  );
}
