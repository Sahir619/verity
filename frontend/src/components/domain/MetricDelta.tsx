import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatArea, formatDelta, formatMhz, formatMw, formatNs } from "@/lib/format";

export type MetricDeltaProps = {
  metric: "area" | "cells" | "clockPeriod" | "power" | "fmax";
  value: number | null;
  baseline: number | null;
  showValue?: boolean;
  size?: "sm" | "md";
  /** Show the metric name before the delta ("Area −12.5%"). Used by cards where no column header exists. */
  showLabel?: boolean;
  /** Hide the arrow when space is tight. The sign and color still carry the direction. Default true. */
  showArrow?: boolean;
};

const LABEL = { area: "Area", cells: "Cells", clockPeriod: "Clock", power: "Power", fmax: "Fmax" } as const;
const LOWER_IS_BETTER = { area: true, cells: true, clockPeriod: true, power: true, fmax: false } as const;
const WORSE_SUFFIX = { area: "(larger)", cells: "(larger)", clockPeriod: "(slower)", power: "(more power)", fmax: "(slower)" } as const;

function formatValue(metric: MetricDeltaProps["metric"], value: number): string {
  switch (metric) {
    case "area":
      return formatArea(value);
    case "cells":
      return Math.round(value).toLocaleString("en-US");
    case "clockPeriod":
      return formatNs(value);
    case "power":
      return formatMw(value);
    case "fmax":
      return formatMhz(value);
  }
}

/**
 * Value and percent change against v1, with direction. Better = pass + arrow, worse = warn + arrow + a plain word
 * ("(slower)"), under 0.5 percent = neutral, no baseline = "baseline", no value = "not measured".
 */
export function MetricDelta({ metric, value, baseline, showValue = false, size = "md", showLabel = false, showArrow = true }: MetricDeltaProps) {
  const textSize = size === "sm" ? "text-xs" : "text-sm";
  const label = showLabel ? <span className="text-fg-muted">{LABEL[metric]}</span> : null;

  if (value === null) {
    return (
      <span className={cn("inline-flex items-baseline gap-1.5 text-fg-subtle", textSize)}>
        {label}
        <span>not measured</span>
      </span>
    );
  }

  const valueText = showValue ? <span className="font-mono text-foreground">{formatValue(metric, value)}</span> : null;

  if (baseline === null) {
    return (
      <span className={cn("inline-flex items-baseline gap-1.5", textSize)}>
        {label}
        {valueText}
        <span className="text-fg-muted">baseline</span>
      </span>
    );
  }

  const delta = formatDelta(value, baseline, { lowerIsBetter: LOWER_IS_BETTER[metric] });
  const Arrow = value < baseline ? ArrowDown : ArrowUp;
  const tone =
    delta.direction === "better" ? "text-pass" : delta.direction === "worse" ? "text-warn" : "text-fg-muted";
  return (
    <span className={cn("inline-flex items-baseline gap-1.5 whitespace-nowrap", textSize)}>
      {label}
      {valueText}
      <span className={cn("inline-flex items-center gap-0.5 font-mono", tone)}>
        {delta.direction === "neutral" || !showArrow ? null : <Arrow aria-hidden className="size-3" />}
        {delta.text}
      </span>
      {delta.direction === "worse" ? (
        <span className={cn("text-warn", size === "sm" && "sr-only")}>{WORSE_SUFFIX[metric]}</span>
      ) : null}
      <span className="sr-only">versus v1</span>
    </span>
  );
}
