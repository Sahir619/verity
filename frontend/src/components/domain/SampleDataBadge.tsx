import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Marks placeholder numbers. Every metric surface shows it while the run log has sample: true. */
export function SampleDataBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="secondary"
          tabIndex={0}
          className="h-auto cursor-help rounded-sm bg-warn-muted px-1.5 py-0.5 text-xs font-medium text-warn"
        >
          Sample data
        </Badge>
      </TooltipTrigger>
      <TooltipContent>Placeholder numbers until the real run is recorded. Not results.</TooltipContent>
    </Tooltip>
  );
}
