import type { CandidateStatus, PPA } from "@/lib/contracts/types";
import { formatArea, formatMhz, formatMw, formatNs } from "@/lib/format";
import { MetricDelta, type MetricDeltaProps } from "./MetricDelta";

export type PpaTableProps = {
  ppa: PPA | null;
  baseline: PPA | null;
  status: CandidateStatus;
  isBaseline: boolean;
};

type Row = { label: string; metric: MetricDeltaProps["metric"]; value: number | null; base: number | null; text: string | null };

function fmax(period: number | null): number | null {
  return period && period > 0 ? 1000 / period : null;
}

function buildRows(ppa: PPA | null, baseline: PPA | null): Row[] {
  const p = ppa;
  const b = baseline;
  const fm = fmax(p?.clockPeriodNs ?? null);
  const bfm = fmax(b?.clockPeriodNs ?? null);
  return [
    { label: "Area", metric: "area", value: p?.areaUm2 ?? null, base: b?.areaUm2 ?? null, text: p?.areaUm2 != null ? formatArea(p.areaUm2) : null },
    { label: "Cells", metric: "cells", value: p?.cellCount ?? null, base: b?.cellCount ?? null, text: p?.cellCount != null ? p.cellCount.toLocaleString("en-US") : null },
    { label: "Clock period", metric: "clockPeriod", value: p?.clockPeriodNs ?? null, base: b?.clockPeriodNs ?? null, text: p?.clockPeriodNs != null ? formatNs(p.clockPeriodNs) : null },
    { label: "Fmax", metric: "fmax", value: fm, base: bfm, text: fm !== null ? formatMhz(fm) : null },
    { label: "Power", metric: "power", value: p?.powerMw ?? null, base: b?.powerMw ?? null, text: p?.powerMw != null ? formatMw(p.powerMw) : null },
  ];
}

function emptyMessage(status: CandidateStatus): string {
  if (status === "fail" || status === "fixing" || status === "rejected") {
    return "Not measured. Versions that fail the locked tests are never synthesized.";
  }
  return "Not measured yet. Synthesis runs after a version passes the locked tests.";
}

/** Area, cells, clock period, Fmax and power for one version, each with its change against v1 (datasheet table). */
export function PpaTable({ ppa, baseline, status, isBaseline }: PpaTableProps) {
  if (!ppa) {
    return <p className="dp-empty">{emptyMessage(status)}</p>;
  }
  const rows = buildRows(ppa, baseline);
  return (
    <div className="tbl-wrap">
      <table className="dtbl ppa-tbl">
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col" className="num">
              Value
            </th>
            <th scope="col" className="num">
              Versus v1
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td className="num mono">{row.text ?? <span className="dp-na">not measured</span>}</td>
              <td className="num">
                {isBaseline ? (
                  <span className="dp-na">baseline</span>
                ) : baseline === null ? (
                  <span className="dp-na">no baseline yet</span>
                ) : (
                  <MetricDelta metric={row.metric} value={row.value} baseline={row.base} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
