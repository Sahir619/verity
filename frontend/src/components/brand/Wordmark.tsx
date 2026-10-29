import { cn } from "@/lib/utils";

export type WordmarkProps = { size?: "sm" | "md" | "lg" | "display"; className?: string };

const SIZE = {
  sm: "text-lg",
  md: "text-2xl",
  lg: "text-4xl",
  display: "text-display",
} as const;

/**
 * "Verity" with the dot of the i replaced by a Verity Green square. Built as text: Ver + dotless i
 * (U+0131) with an absolutely positioned square + ty. Exposes one accessible name and hides the pieces.
 */
export function Wordmark({ size = "md", className }: WordmarkProps) {
  return (
    <span
      role="img"
      aria-label="Verity"
      className={cn("inline-block leading-none font-extrabold tracking-[-0.02em] whitespace-nowrap text-foreground", SIZE[size], className)}
    >
      <span aria-hidden>Ver</span>
      <span aria-hidden className="relative inline-block">
        {"ı"}
        <span className="absolute top-[0.11em] left-1/2 size-[0.2em] -translate-x-1/2 bg-pass" />
      </span>
      <span aria-hidden>ty</span>
    </span>
  );
}
