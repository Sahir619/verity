import { cn } from "@/lib/utils";

export type PoweredByProps = { layout?: "inline" | "stacked" };

/** Text-only credit. Logos stay out until the NVIDIA and Nebius usage rules are checked. */
export function PoweredBy({ layout = "inline" }: PoweredByProps) {
  if (layout === "stacked") {
    return (
      <p className="flex flex-col gap-0.5 text-sm text-fg-muted">
        <span className="text-xs text-fg-subtle">Built on</span>
        <span className="font-medium text-foreground">NVIDIA Nemotron</span>
        <span>on Nebius Token Factory</span>
      </p>
    );
  }
  return (
    <p className={cn("text-sm text-fg-muted")}>
      <span className="font-medium text-foreground">NVIDIA Nemotron</span> on Nebius Token Factory
    </p>
  );
}
