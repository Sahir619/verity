"use client";

import { Download } from "lucide-react";
import { Callout } from "@/components/domain/Callout";
import { Button } from "@/components/ui/button";
import { useRunZip } from "@/components/sections/shared/use-run-zip";

export type ExportPanelProps = { winnerLabel: string | null; baselineLabel: string | null };

export function ExportPanel({ winnerLabel, baselineLabel }: ExportPanelProps) {
  const { state, download } = useRunZip();
  const contents = [
    winnerLabel ? `${winnerLabel}.v (winner)` : null,
    baselineLabel ? `${baselineLabel}.v (baseline)` : null,
    "testbench",
    "golden model",
    "synthesis and EQY reports",
    "run log (JSON)",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <section aria-labelledby="export-heading" className="space-y-3 rounded-lg border bg-surface p-5">
      <h2 id="export-heading" className="text-xl font-bold text-foreground">
        Download
      </h2>
      {state === "error" ? (
        <Callout
          tone="fail"
          title="Could not build the zip."
          actions={
            <Button variant="outline" className="h-8 px-3" onClick={() => void download()}>
              Try again
            </Button>
          }
        />
      ) : null}
      <Button className="h-10 w-full gap-2 px-4" disabled={state === "busy"} onClick={() => void download()}>
        <Download aria-hidden />
        {state === "busy" ? "Preparing zip..." : "Download design (.zip)"}
      </Button>
      <p className="text-sm text-fg-muted">Contains: {contents}.</p>
    </section>
  );
}
