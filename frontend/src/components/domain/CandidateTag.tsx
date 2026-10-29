import { Check, Crown, Star, Wrench, X, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type CandidateTagProps = {
  tag: "baseline" | "elite" | "winner" | "rejected" | "fixed";
  count?: number;
  by?: "ai" | "user";
  local?: boolean;
};

function fixedLabel(count: number): string {
  if (count === 1) return "Fixed once";
  if (count === 2) return "Fixed twice";
  return `Fixed ${count} times`;
}

/** Role tags: baseline, elite, winner (AI or your pick), rejected and fixed N times. */
export function CandidateTag({ tag, count = 1, by = "ai", local }: CandidateTagProps) {
  let label: string;
  let Icon: LucideIcon | null = null;
  let tone = "bg-surface-2 text-fg-muted";
  switch (tag) {
    case "baseline":
      label = "Baseline";
      Icon = Check;
      break;
    case "elite":
      label = "Elite";
      Icon = Star;
      break;
    case "winner":
      label = by === "user" ? "Winner (your pick)" : "Winner";
      Icon = Crown;
      tone = "bg-pass-muted text-pass";
      break;
    case "rejected":
      label = "Rejected";
      Icon = X;
      break;
    case "fixed":
      label = fixedLabel(count);
      Icon = Wrench;
      break;
  }
  return (
    <Badge
      variant="secondary"
      title={local ? "Local to this replay. Restarting the replay resets it." : undefined}
      className={cn("h-auto gap-1 rounded-sm px-1.5 py-0.5 text-xs font-medium", tone)}
    >
      {Icon ? <Icon aria-hidden className="size-3" /> : null}
      {label}
    </Badge>
  );
}
