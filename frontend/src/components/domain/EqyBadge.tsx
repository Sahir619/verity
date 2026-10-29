import { CircleAlert, Equal, TriangleAlert, X, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Equivalence } from "@/lib/contracts/types";
import { TONE_CLASS, type Tone } from "./status-meta";

export type EqyBadgeProps = { eqy?: Equivalence; againstLabel: string; size?: "sm" | "md" };

function metaFor(eqy: Equivalence | undefined, against: string): { text: string; Icon: LucideIcon; tone: Tone } {
  if (!eqy) return { text: "Not checked in this recording", Icon: CircleAlert, tone: "neutral" };
  switch (eqy.status) {
    case "proven":
      return { text: `Proven equal to ${against}`, Icon: Equal, tone: "pass" };
    case "not_equivalent":
      return { text: `Not equal to ${against}`, Icon: X, tone: "fail" };
    case "inconclusive":
      return { text: "Proof inconclusive", Icon: TriangleAlert, tone: "warn" };
    case "not_run":
      return { text: "Not checked yet", Icon: CircleAlert, tone: "neutral" };
  }
}

/** Equivalence result from Yosys EQY. "Proven" is used only for this result, never for simulation. */
export function EqyBadge({ eqy, againstLabel, size = "md" }: EqyBadgeProps) {
  const { text, Icon, tone } = metaFor(eqy, againstLabel);
  return (
    <Badge
      variant="secondary"
      className={cn(
        "h-auto rounded-sm font-medium",
        size === "sm" ? "gap-1 px-1.5 py-0.5 text-xs" : "gap-1.5 px-2 py-1 text-sm",
        TONE_CLASS[tone],
      )}
    >
      <Icon aria-hidden className={size === "sm" ? "size-3" : "size-4"} />
      {text}
    </Badge>
  );
}
