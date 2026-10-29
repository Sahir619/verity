import type { ReactNode } from "react";
import { Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export type RunLedgerProps = { rows: { label: string; value: ReactNode; hint?: string }[] };

/** Definition list summary of a run. A ledger, not a grid of hero metrics. */
export function RunLedger({ rows }: RunLedgerProps) {
  return (
    <dl className="divide-y divide-border rounded-lg border border-border bg-surface">
      {rows.map((row) => (
        <div key={row.label} className="flex items-baseline justify-between gap-4 px-4 py-2.5">
          <dt className="flex items-center gap-1.5 text-sm text-fg-muted">
            {row.label}
            {row.hint ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`About ${row.label}`}
                    className="inline-flex size-6 items-center justify-center rounded-md text-fg-subtle hover:bg-surface-2 hover:text-foreground"
                  >
                    <Info aria-hidden className="size-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent>{row.hint}</TooltipContent>
              </Tooltip>
            ) : null}
          </dt>
          <dd className="text-right text-sm text-foreground">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
