"use client";

import { useId, useState, type ReactNode } from "react";
import { formatSmallUsd } from "../bench/chat";
import { BarRows, type BarRowData, type BarSeries } from "./BarRows";
import { type DeltaRow, type SpendRow } from "./derive";

const MINUS = "−";

/** "−1.6%", "+3.0%", "0.0%" for a percent number. The real minus sign, as everywhere else in the app. */
function signedPct(p: number): string {
  const text = `${Math.abs(p).toFixed(1)}%`;
  return p > 0 ? `+${text}` : p < 0 ? `${MINUS}${text}` : text;
}

const DELTA_SERIES: BarSeries[] = [
  { key: "area", word: "area", tip: "area vs v1", cls: "w-s-area" },
  { key: "clock", word: "clock", tip: "clock period vs v1", cls: "w-s-clock" },
];
const SPEND_SERIES: BarSeries[] = [{ key: "usd", word: "", tip: "spent", cls: "w-s-spend" }];

/**
 * A chart with its title, one sentence that says how to read it, an optional legend, and the table that holds the same
 * numbers (the chart's text alternative, reachable by keyboard). Filters are not here: they sit above, in the page.
 */
function ChartFrame({
  title,
  caption,
  legend,
  chart,
  table,
}: {
  title: string;
  caption: string;
  legend?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  const bodyId = useId();
  return (
    <figure className="w-fig">
      <div className="w-fig-top">
        <h2 className="w-h2">{title}</h2>
        <button type="button" className="b-link" aria-expanded={asTable} aria-controls={bodyId} onClick={() => setAsTable((t) => !t)}>
          {asTable ? "View as chart" : "View as table"}
        </button>
      </div>
      <figcaption className="w-cap">{caption}</figcaption>
      {legend}
      <div id={bodyId}>{asTable ? table : chart}</div>
    </figure>
  );
}

function Legend({ series }: { series: BarSeries[] }) {
  return (
    <ul className="w-legend" aria-label="Legend">
      {series.map((s) => (
        <li key={s.key}>
          <i className={`w-key ${s.cls}`} aria-hidden />
          {s.tip}
        </li>
      ))}
    </ul>
  );
}

/** Area and clock period of each run's winner against that run's own v1. Left of the 0% line is better. */
export function DeltaChart({ rows }: { rows: DeltaRow[] }) {
  const data: BarRowData[] = rows.map((r) => ({
    id: r.id,
    title: r.title,
    bars: [
      ...(r.area !== null ? [{ series: "area", value: r.area, text: signedPct(r.area) }] : []),
      ...(r.clock !== null ? [{ series: "clock", value: r.clock, text: signedPct(r.clock) }] : []),
    ],
  }));
  return (
    <ChartFrame
      title="Change against v1"
      caption="Left of 0% is better."
      legend={<Legend series={DELTA_SERIES} />}
      chart={
        <BarRows
          rows={data}
          series={DELTA_SERIES}
          zeroLine
          tickText={(v) => `${v === 0 ? "" : v > 0 ? "+" : MINUS}${Math.abs(v)}%`}
          label={`Bar chart of area and clock change against v1 for ${rows.length} ${rows.length === 1 ? "run" : "runs"}. The same numbers are in the table view.`}
        />
      }
      table={
        <table className="w-table">
          <caption className="sr">Area and clock period of each run&apos;s winner, in percent against that run&apos;s own v1</caption>
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Area vs v1</th>
              <th scope="col">Clock period vs v1</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <th scope="row">{r.title}</th>
                <td className="m">{r.area === null ? "Not measured" : signedPct(r.area)}</td>
                <td className="m">{r.clock === null ? "Not measured" : signedPct(r.clock)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  );
}

/** What each run actually spent on model calls, in US dollars. */
export function SpendChart({ rows }: { rows: SpendRow[] }) {
  const data: BarRowData[] = rows.map((r) => ({ id: r.id, title: r.title, bars: [{ series: "usd", value: r.usd, text: formatSmallUsd(r.usd) }] }));
  return (
    <ChartFrame
      title="Spend per run"
      caption="What each run spent on AI model calls."
      chart={
        <BarRows
          rows={data}
          series={SPEND_SERIES}
          zeroLine={false}
          tickText={(v, step) => (v === 0 ? "$0" : `$${v.toFixed(Math.max(2, Math.ceil(-Math.log10(step))))}`)}
          label={`Bar chart of what each of ${rows.length} ${rows.length === 1 ? "run" : "runs"} spent, in US dollars. The same numbers are in the table view.`}
        />
      }
      table={
        <table className="w-table">
          <caption className="sr">What each run spent on AI model calls, in US dollars</caption>
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Spent</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <th scope="row">{r.title}</th>
                <td className="m">{formatSmallUsd(r.usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  );
}
