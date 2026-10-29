import { Info } from "lucide-react";

export type HonestyNoteProps = { variant?: "inline" | "block"; tools?: string[] };

/** The baseline and tools label that sits near every metric. */
export function HonestyNote({ variant = "inline", tools }: HonestyNoteProps) {
  const toolText = tools && tools.length > 0 ? ` (${tools.join(", ")})` : "";
  if (variant === "inline") {
    return <p className="text-xs text-fg-subtle">{`Relative to our own v1, open-source tools${toolText}.`}</p>;
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 p-3 text-sm text-fg-muted">
      <Info aria-hidden className="mt-0.5 size-4 shrink-0" />
      <p>{`Relative to our own v1, open-source tools${toolText}. Not signoff.`}</p>
    </div>
  );
}
