import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RunMode } from "@/lib/contracts/types";
import type { VisualStatus } from "@/lib/contracts/view-types";
import { StatusSquare } from "./StatusSquare";

export type ModeBadgeProps = { mode: RunMode; connection?: "connecting" | "open" | "retrying" | "closed" | "error" };

type Meta = { word: string; visual: VisualStatus; tone: string };

function metaFor({ mode, connection }: ModeBadgeProps): Meta {
  if (mode === "replay") return { word: "Replay", visual: "idle", tone: "bg-surface-2 text-fg-muted" };
  switch (connection) {
    case "connecting":
      return { word: "Live, connecting", visual: "running", tone: "bg-info-muted text-info" };
    case "retrying":
      return { word: "Live, reconnecting", visual: "running", tone: "bg-info-muted text-info" };
    case "error":
      return { word: "Live, connection lost", visual: "fail", tone: "bg-fail-muted text-fail" };
    case "closed":
      return { word: "Live, ended", visual: "idle", tone: "bg-surface-2 text-fg-muted" };
    default:
      return { word: "Live", visual: "running", tone: "bg-info-muted text-info" };
  }
}

/** Replay or Live, with the live connection state. The square blinks only while a connection is being made. */
export function ModeBadge(props: ModeBadgeProps) {
  const meta = metaFor(props);
  const blink = props.mode === "live" && (props.connection === "connecting" || props.connection === "retrying");
  return (
    <Badge variant="secondary" className={cn("h-auto gap-1.5 rounded-sm px-2 py-1 text-xs font-medium", meta.tone)}>
      <StatusSquare status={meta.visual} size="sm" animate={blink} />
      {meta.word}
    </Badge>
  );
}
