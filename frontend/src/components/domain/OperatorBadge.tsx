import { Compass, FileCode, Gauge, Minimize2, Wrench, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { CreatedBy } from "@/lib/contracts/types";

export type OperatorBadgeProps = { by: CreatedBy | "FIX"; size?: "sm" | "md" };

const META: Record<CreatedBy | "FIX", { label: string; icon: LucideIcon; loud: boolean }> = {
  INIT: { label: "First draft", icon: FileCode, loud: false },
  SIMPLIFY: { label: "SIMPLIFY", icon: Minimize2, loud: true },
  OPTIMIZE: { label: "OPTIMIZE", icon: Gauge, loud: true },
  EXPLORE: { label: "EXPLORE", icon: Compass, loud: true },
  FIX: { label: "FIX", icon: Wrench, loud: true },
};

/** The operator that created a version. Uppercase only for the four operator names. */
export function OperatorBadge({ by, size = "md" }: OperatorBadgeProps) {
  const { label, icon: Icon, loud } = META[by];
  return (
    <Badge
      variant="outline"
      className={cn(
        "h-auto rounded-sm border-border font-medium text-fg-muted",
        size === "sm" ? "gap-1 px-1.5 py-0.5 text-xs" : "gap-1.5 px-2 py-1 text-xs",
        loud && "tracking-wider",
      )}
    >
      <Icon aria-hidden className="size-3" />
      {label}
    </Badge>
  );
}
