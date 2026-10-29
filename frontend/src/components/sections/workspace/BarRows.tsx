"use client";

import { useEffect, useRef, useState } from "react";

/** One bar in a row. `text` is the value as the reader sees it ("−1.6%", "$0.19"). */
export type BarValue = { series: string; value: number; text: string };
export type BarRowData = { id: string; title: string; bars: BarValue[] };
/** `tip` finishes the tooltip line after the value ("area vs v1"); `cls` is the CSS class that paints the mark. */
export type BarSeries = { key: string; word: string; tip: string; cls: string };

const BAR_H = 10;
const BAR_GAP = 3;
const TITLE_H = 18;
const ROW_GAP = 16;
const AXIS_H = 22;
const PAD_X = 4;
/** Room at the left edge for half of the first tick label, which is centred on its tick. */
const TICK_PAD = 20;
/** Width of one character of the 11px data face, for sizing the value labels and the ticks. */
const CHAR_W = 6.7;
const MIN_BAR = 1.5;
const TIP_W = 220;

/** The width of an element, kept current. 0 until it has been measured (the first paint has no chart). */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

/** A step of 1, 2 or 5 times a power of ten, so the span holds about `count` of them. */
function niceStep(span: number, count: number): number {
  const raw = span / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

/** A bar from its baseline x0 to its tip x1: square at the baseline, 4px round at the tip. */
function barPath(x0: number, x1: number, y: number, h: number): string {
  const dir = x1 >= x0 ? 1 : -1;
  const len = Math.max(MIN_BAR, Math.abs(x1 - x0));
  const tip = x0 + dir * len;
  const r = Math.min(4, len, h / 2);
  return [
    `M${x0} ${y}`,
    `H${tip - dir * r}`,
    `Q${tip} ${y} ${tip} ${y + r}`,
    `V${y + h - r}`,
    `Q${tip} ${y + h} ${tip - dir * r} ${y + h}`,
    `H${x0}`,
    "Z",
  ].join("");
}

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 3))}...`;
}

/**
 * Horizontal bars, one row per run: the run's name on its own line, then one thin bar per series growing from a single
 * baseline (the 0 line when `zeroLine`, else the left edge). Plain SVG sized to its container in pixels, so text stays at
 * its real size on a phone. Marks use classes (no colour here); the value sits at the tip of each bar. Hovering or
 * focusing a row shows every value of that run in one tooltip. The same numbers are in the table the caller offers.
 */
export function BarRows({
  rows,
  series,
  zeroLine,
  tickText,
  label,
}: {
  rows: BarRowData[];
  series: BarSeries[];
  /** The bars run both ways from a 0 line (changes against v1); otherwise they grow from the left (amounts). */
  zeroLine: boolean;
  /** `step` is the gap between ticks, so a label can carry as many decimals as the step needs. */
  tickText(value: number, step: number): string;
  /** What the chart is, for a screen reader; each row then says its own numbers. */
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [tip, setTip] = useState<{ id: string; x: number; y: number } | null>(null);

  const values = rows.flatMap((r) => r.bars.map((b) => b.value));
  const needLeft = values.some((v) => v < 0);
  const needRight = values.some((v) => v >= 0);
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  if (hi === lo) {
    // Nothing to compare yet (every value is 0): a symmetric axis for changes, a short one for amounts.
    if (zeroLine) lo -= 1;
    hi += 1;
  }

  // Room for a value label at the tip of the longest bar, on each side that has bars; then as many ticks as fit.
  const wordOf = new Map(series.map((s) => [s.key, s.word]));
  const wordTip = new Map(series.map((s) => [s.key, s.tip]));
  const labelOf = (b: BarValue) => `${wordOf.get(b.series) ?? ""} ${b.text}`.trim();
  const labelW = Math.ceil(Math.max(0, ...rows.flatMap((r) => r.bars.map((b) => labelOf(b).length))) * CHAR_W) + 10;
  const left = needLeft ? PAD_X + labelW : TICK_PAD;
  const right = needRight ? PAD_X + labelW : TICK_PAD;
  const step = niceStep(hi - lo, Math.max(2, Math.min(5, Math.floor((width - left - right) / 56))));
  lo = Math.floor(lo / step + 1e-9) * step;
  hi = Math.ceil(hi / step - 1e-9) * step;

  const x0 = left;
  const x1 = width - right;
  const scale = x1 > x0 ? (x1 - x0) / (hi - lo) : 0;
  const px = (v: number) => x0 + (v - lo) * scale;
  const zeroX = px(0);

  const rowH = (r: BarRowData) => TITLE_H + r.bars.length * BAR_H + (r.bars.length - 1) * BAR_GAP + ROW_GAP;
  const heights = rows.map(rowH);
  const tops = heights.map((_, i) => AXIS_H + heights.slice(0, i).reduce((a, b) => a + b, 0));
  const height = AXIS_H + heights.reduce((a, b) => a + b, 0);

  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 1000; v += step) ticks.push(Math.abs(v) < step / 1000 ? 0 : Number(v.toFixed(6)));

  const place = (id: string, clientX: number, clientY: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (rect) setTip({ id, x: clientX - rect.left, y: clientY - rect.top });
  };
  const placeAtRow = (id: string, top: number) => setTip({ id, x: Math.min(width - 16, x0 + 24), y: top });
  const shown = tip ? rows.find((r) => r.id === tip.id) : undefined;
  const maxChars = Math.max(8, Math.floor((width - PAD_X * 2) / 6.8));

  return (
    <div className="w-plot" ref={box}>
      {width > 0 && scale > 0 ? (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-label={label}>
          <g className="w-grid" aria-hidden>
            {ticks.map((v) => (
              <g key={v}>
                <line className={zeroLine && v === 0 ? "w-zero" : undefined} x1={px(v)} x2={px(v)} y1={AXIS_H - 4} y2={height - ROW_GAP + 6} />
                <text x={px(v)} y={11} textAnchor="middle">
                  {tickText(v, step)}
                </text>
              </g>
            ))}
          </g>
          {rows.map((r, i) => {
            const top = tops[i];
            return (
              <g
                key={r.id}
                className="w-row"
                role="img"
                aria-label={`${r.title}: ${r.bars.map((b) => `${b.text} ${wordTip.get(b.series) ?? ""}`.trim()).join(", ")}`}
                tabIndex={0}
                data-on={tip?.id === r.id ? "" : undefined}
                onPointerMove={(e) => place(r.id, e.clientX, e.clientY)}
                onPointerLeave={() => setTip(null)}
                onFocus={() => placeAtRow(r.id, top + TITLE_H)}
                onBlur={() => setTip(null)}
              >
                <rect className="w-hit" x={0} y={top - 4} width={width} height={rowH(r) - ROW_GAP + 8} rx={6} />
                <text className="w-name" x={PAD_X} y={top + 11}>
                  {clip(r.title, maxChars)}
                </text>
                {r.bars.map((b, j) => {
                  const y = top + TITLE_H + j * (BAR_H + BAR_GAP);
                  const tipX = px(b.value);
                  const neg = b.value < 0;
                  return (
                    <g key={b.series} aria-hidden>
                      <path className={`w-bar ${series.find((s) => s.key === b.series)?.cls ?? ""}`} d={barPath(zeroX, tipX, y, BAR_H)} />
                      <text className="w-val" x={neg ? tipX - 6 : tipX + 6} y={y + BAR_H - 1} textAnchor={neg ? "end" : "start"}>
                        {labelOf(b)}
                      </text>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      ) : (
        <div className="w-plot-wait" style={{ height: 80 }} />
      )}
      {tip && shown ? (
        <div
          className="w-tip"
          role="status"
          style={{ left: Math.min(tip.x + 14, Math.max(8, width - TIP_W - 8)), top: tip.y + 14 }}
        >
          <p className="w-tip-t">{shown.title}</p>
          {shown.bars.map((b) => {
            const s = series.find((x) => x.key === b.series);
            return (
              <p key={b.series} className="w-tip-v">
                <i className={`w-key ${s?.cls ?? ""}`} aria-hidden />
                <b>{b.text}</b> {s?.tip}
              </p>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
