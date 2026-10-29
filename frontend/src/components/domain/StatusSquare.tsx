"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import type { VisualStatus } from "@/lib/contracts/view-types";

export type StatusSquareProps = { status: VisualStatus; size?: "sm" | "md"; animate?: boolean; label?: string };

const FILL: Record<VisualStatus, string> = {
  idle: "border border-border-strong bg-transparent",
  queued: "border border-fg-subtle bg-transparent",
  running: "bg-info",
  pass: "bg-pass",
  fail: "bg-fail",
  warn: "bg-warn",
  rejected: "bg-fg-subtle",
  locked: "bg-fg-muted",
  done: "bg-pass",
};

/**
 * The square status indicator (2px radius). Blinks only while running, pops once when it changes to pass or fail,
 * and is static under reduced motion (the global CSS zeroes animation durations). Meaning always comes from the
 * paired word or icon next to it; pass a label when the square stands alone.
 */
export function StatusSquare({ status, size = "md", animate = true, label }: StatusSquareProps) {
  // Remember the first status so the pop plays only on later changes, not on mount or on a seek.
  const [initial] = useState(status);
  const pop = animate && status !== initial && (status === "pass" || status === "fail");
  return (
    <span
      key={status}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      data-status={status}
      className={cn(
        "inline-block shrink-0 rounded-[2px]",
        size === "sm" ? "size-2" : "size-2.5",
        FILL[status],
        animate && status === "running" && "animate-status-blink",
        pop && "animate-status-pop",
      )}
    />
  );
}
