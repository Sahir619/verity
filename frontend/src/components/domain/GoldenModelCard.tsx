import { Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { StatusSquare } from "./StatusSquare";

export type GoldenModelCardProps = {
  testCount: number;
  files: string[];
  status: "pending" | "locked";
  variant: "node" | "full" | "mini";
};

/** The locked answer key. Locked cards have a double border (1px border plus an inner ring). */
export function GoldenModelCard({ testCount, files, status, variant }: GoldenModelCardProps) {
  const locked = status === "locked";
  const frame = cn(
    "box-border rounded-lg border bg-surface transition-colors duration-200",
    locked ? "border-border ring-1 ring-border-strong ring-inset" : "border-dashed border-border-strong",
  );
  const summary = locked ? `${testCount} tests, locked` : "Locking";

  if (variant === "mini") {
    return (
      <div className={cn(frame, "flex items-center gap-3 px-3 py-2.5")}>
        <Lock aria-hidden className="size-4 shrink-0 text-fg-muted" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">Golden model</p>
          <p className="text-xs text-fg-muted">{summary}</p>
        </div>
      </div>
    );
  }

  if (variant === "node") {
    return (
      <div className={cn(frame, "flex h-[88px] w-[240px] flex-col justify-between overflow-hidden p-2.5")}>
        <div className="flex h-6 items-center gap-1.5">
          <Lock aria-hidden className="size-3.5 shrink-0 text-fg-muted" />
          <span className="text-sm font-medium text-foreground">Golden model</span>
        </div>
        <p className="flex items-center gap-1.5 text-xs text-fg-muted">
          {locked ? null : <StatusSquare status="running" size="sm" />}
          {summary}
        </p>
        <div className="flex h-5 items-center">
          <Badge variant="outline" className="h-auto rounded-sm px-1.5 py-0.5 text-xs font-medium text-fg-muted">
            AI cannot edit
          </Badge>
        </div>
      </div>
    );
  }

  return (
    <div className={cn(frame, "flex flex-col gap-3 p-4")}>
      <div className="flex items-center gap-2">
        <Lock aria-hidden className="size-5 text-fg-muted" />
        <h3 className="text-xl font-bold text-foreground">Golden model</h3>
      </div>
      <p className="text-sm text-foreground">{summary}</p>
      <p className="text-sm text-fg-muted">The agent cannot edit these files. They are the answer key.</p>
      {files.length > 0 ? (
        <ul className="flex flex-col gap-1 font-mono text-xs text-fg-muted">
          {files.map((file) => (
            <li key={file} className="flex items-center gap-1.5">
              <Lock aria-hidden className="size-3 shrink-0" />
              {file}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
