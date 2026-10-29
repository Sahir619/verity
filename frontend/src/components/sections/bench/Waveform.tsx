"use client";

import type { Waves } from "./artifacts";

/** An empty expectation means the test checks nothing in that cycle, so it can never differ. */
const cellDiffers = (want: string | undefined, got: string) => want !== undefined && want !== "" && want !== got;

/**
 * One test case, cycle by cycle: each signal in a row, the value the locked golden model expects next to what the Verilog
 * gave. A cell where they differ gets an outline and the word "differs" for a screen reader; the first failing cycle is
 * named in its column. No colour carries meaning. Values are shown exactly as the run sent them ("x" is unknown).
 */
export function Waveform({ waves, label }: { waves: Waves; label: string }) {
  const len = Math.max(...waves.signals.map((s) => Math.max(s.got.length, s.expected?.length ?? 0)));
  const cycles = Array.from({ length: len }, (_, i) => waves.startCycle + i);
  const fail = waves.failCycle;
  const title =
    fail !== null ? `Test ${waves.case} failed at cycle ${fail}` : `Every test passes. This is the first directed test, ${waves.case}.`;
  const rows = waves.signals.flatMap((s) =>
    s.expected
      ? [
          { key: `${s.name}:e`, name: `${s.name} expected`, width: s.width, values: s.expected, check: null },
          { key: `${s.name}:g`, name: `${s.name} got`, width: s.width, values: s.got, check: s.expected },
        ]
      : [{ key: s.name, name: s.name, width: s.width, values: s.got, check: null }],
  );
  const anyDiffers = rows.some((r) => r.check !== null && r.values.some((v, i) => cellDiffers(r.check?.[i], v)));
  return (
    <figure className="b-wave">
      <figcaption className="b-wave-h">
        <b>{title}</b>
        <span className="b-dim">
          {label}, cycles {cycles[0]} to {cycles.at(-1)}
        </span>
      </figcaption>
      <div className="b-wave-scroll" role="region" tabIndex={0} aria-label={`Waveform of ${label}, ${title}`}>
        <table className="b-wave-t">
          <thead>
            <tr>
              <th scope="col">cycle</th>
              {cycles.map((c) => (
                <th key={c} scope="col" className="m" data-fail={c === fail ? "" : undefined}>
                  {c}
                  {c === fail ? <small>first fail</small> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} data-got={r.check ? "" : undefined}>
                <th scope="row">
                  {r.name}
                  {r.width > 1 ? <small className="m"> {r.width}b</small> : null}
                </th>
                {cycles.map((_, i) => {
                  const v = r.values[i] ?? "";
                  const differs = r.check !== null && cellDiffers(r.check[i], v);
                  return (
                    <td key={i} className="m" data-differs={differs ? "" : undefined}>
                      {v}
                      {differs ? <span className="sr">, differs, expected {r.check?.[i]}</span> : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="b-note b-dim">
        {anyDiffers
          ? "Outlined: what the Verilog gave differs from what the golden model expects. "
          : rows.some((r) => r.check)
            ? "What the Verilog gave matches what the golden model expects in every cycle shown. "
            : ""}
        {waves.note ?? ""}
      </p>
    </figure>
  );
}
