import { User } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export type HumanInputsCounterProps = { items: { label: string; local?: boolean }[] };

/** "Human inputs: N" with a tooltip that lists each input. */
export function HumanInputsCounter({ items }: HumanInputsCounterProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="inline-flex min-h-6 items-center gap-1.5 rounded-md px-1.5 text-sm text-fg-muted hover:bg-surface-2 hover:text-foreground"
        >
          <User aria-hidden className="size-4" />
          <span>
            Human inputs: <span className="font-mono text-foreground">{items.length}</span>
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm items-start">
        {items.length === 0 ? (
          <span>No human inputs yet.</span>
        ) : (
          <ol className="flex flex-col gap-0.5">
            {items.map((item, i) => (
              <li key={`${i}-${item.label}`}>
                {i + 1}. {item.label}
                {item.local ? " (local to this replay)" : ""}
              </li>
            ))}
          </ol>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
