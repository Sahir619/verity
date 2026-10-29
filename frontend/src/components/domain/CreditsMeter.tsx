import { Coins } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatUsd } from "@/lib/format";
import type { RunMode } from "@/lib/contracts/types";

export type CreditsMeterProps = { mode: RunMode; usedUsd: number; recordedUsd?: number };

/** What the run spent. Replays cost nothing. The spend limit is enforced by the server and never shown as a figure. */
export function CreditsMeter({ mode, usedUsd, recordedUsd }: CreditsMeterProps) {
  if (mode === "replay") {
    const tip =
      recordedUsd !== undefined
        ? `Replays cost nothing. The original run cost ${formatUsd(recordedUsd)}.`
        : "Replays cost nothing.";
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="inline-flex min-h-6 items-center gap-1.5 rounded-md px-1.5 text-sm text-fg-muted hover:bg-surface-2 hover:text-foreground"
          >
            <Coins aria-hidden className="size-4" />
            <span className="font-mono">{formatUsd(0)}</span>
            <span>replay</span>
          </button>
        </TooltipTrigger>
        <TooltipContent>{tip}</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <div className="inline-flex items-center gap-2 text-sm">
      <Coins aria-hidden className="size-4 text-fg-muted" />
      <span className="font-mono text-foreground">{formatUsd(usedUsd)}</span>
      <span className="text-fg-muted">spent</span>
    </div>
  );
}
