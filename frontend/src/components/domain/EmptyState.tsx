import type { ReactNode } from "react";

export type EmptyStateProps = { title: string; body?: string; action?: ReactNode };

/** Teaching empty or error block: says what is missing, why, and what to do next. */
export function EmptyState({ title, body, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-strong px-6 py-10 text-center">
      <h2 className="text-xl font-bold text-foreground">{title}</h2>
      {body ? <p className="max-w-[65ch] text-base text-fg-muted">{body}</p> : null}
      {action ? <div className="mt-2 flex flex-wrap items-center justify-center gap-3">{action}</div> : null}
    </div>
  );
}
