import type { PPA } from "@/lib/contracts/types";
import { formatArea, formatDelta, formatMw, formatNs } from "@/lib/format";
import { SampleDataBadge } from "./SampleDataBadge";

export type PpaDeltaChartProps = {
  baseline: PPA;
  candidate: PPA;
  baselineLabel: string;
  candidateLabel: string;
  sample: boolean;
};

type Metric = {
  label: string;
  base: number | null;
  value: number | null;
  fmt: (x: number) => string;
  worse: string;
};

const W = 520;
const LABEL_W = 92;
const BAR_X = LABEL_W + 4;
const BAR_MAX = 190;
const TEXT_X = BAR_X + BAR_MAX + 12;
const GROUP_H = 58;
const TOP = 28;

/** v1 against one version as plain SVG bars (v1 = 100). Every bar also has its value as text. */
export function PpaDeltaChart({ baseline, candidate, baselineLabel, candidateLabel, sample }: PpaDeltaChartProps) {
  const metrics: Metric[] = [
    { label: "Area", base: baseline.areaUm2, value: candidate.areaUm2, fmt: formatArea, worse: "(larger)" },
    { label: "Clock period", base: baseline.clockPeriodNs, value: candidate.clockPeriodNs, fmt: formatNs, worse: "(slower)" },
    { label: "Power", base: baseline.powerMw, value: candidate.powerMw, fmt: formatMw, worse: "(more power)" },
  ];

  const pcts = metrics.map((m) => (m.base && m.value !== null ? (m.value / m.base) * 100 : null));
  const scaleMax = Math.max(100, ...pcts.filter((p): p is number => p !== null));
  const unit = BAR_MAX / scaleMax;
  const height = TOP + metrics.length * GROUP_H;

  const summary = metrics
    .map((m) => {
      if (m.base === null || m.value === null) return `${m.label}: not measured.`;
      const d = formatDelta(m.value, m.base, { lowerIsBetter: true });
      return `${m.label}: ${baselineLabel} ${m.fmt(m.base)}, ${candidateLabel} ${m.fmt(m.value)}, ${d.text} versus ${baselineLabel}.`;
    })
    .join(" ");

  return (
    <figure className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`${baselineLabel} is 100 on every row. Shorter is better. ${summary}`}
        className="w-full max-w-[34rem] text-xs"
      >
        <rect x={BAR_X} y={6} width={10} height={10} rx={2} className="fill-chart-5" />
        <text x={BAR_X + 16} y={15} className="fill-fg-muted">
          {baselineLabel} = 100
        </text>
        <text x={TEXT_X} y={15} className="fill-fg-subtle">
          Shorter is better
        </text>
        {metrics.map((m, i) => {
          const y = TOP + i * GROUP_H;
          const pct = pcts[i];
          const measured = pct !== null && m.base !== null && m.value !== null;
          const d = measured ? formatDelta(m.value as number, m.base as number, { lowerIsBetter: true }) : null;
          const fill = d?.direction === "better" ? "fill-pass" : d?.direction === "worse" ? "fill-warn" : "fill-fg-muted";
          return (
            <g key={m.label}>
              <text x={0} y={y + 24} className="fill-foreground">
                {m.label}
              </text>
              {measured ? (
                <>
                  <rect x={BAR_X} y={y + 4} width={100 * unit} height={16} rx={2} className="fill-chart-5" />
                  <text x={TEXT_X} y={y + 16} className="fill-fg-muted font-mono">
                    {m.fmt(m.base as number)}
                  </text>
                  <rect x={BAR_X} y={y + 26} width={(pct as number) * unit} height={16} rx={2} className={fill} />
                  <text x={TEXT_X} y={y + 38} className="fill-foreground font-mono">
                    {m.fmt(m.value as number)} {d?.text}
                    {d?.direction === "worse" ? ` ${m.worse}` : ""}
                  </text>
                </>
              ) : (
                <text x={BAR_X} y={y + 24} className="fill-fg-subtle">
                  not measured
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-subtle">
        <span>Top bar: {baselineLabel}. Bottom bar: {candidateLabel}.</span>
        {sample ? <SampleDataBadge /> : null}
      </figcaption>
    </figure>
  );
}
