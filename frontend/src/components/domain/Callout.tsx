import type { ReactNode } from "react";
import { Check, CircleAlert, Info, TriangleAlert, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type CalloutProps = { tone: "info" | "warn" | "fail" | "pass"; title: string; body?: ReactNode; actions?: ReactNode };

const TONES: Record<CalloutProps["tone"], { box: string; icon: string; Icon: LucideIcon }> = {
  info: { box: "border-info/50 bg-info-muted", icon: "text-info", Icon: Info },
  warn: { box: "border-warn/50 bg-warn-muted", icon: "text-warn", Icon: TriangleAlert },
  fail: { box: "border-fail/50 bg-fail-muted", icon: "text-fail", Icon: CircleAlert },
  pass: { box: "border-pass/50 bg-pass-muted", icon: "text-pass", Icon: Check },
};

/** State banner: a full 1px border plus a tinted fill (never a side stripe), with an icon so tone is not color alone. */
export function Callout({ tone, title, body, actions }: CalloutProps) {
  const { box, icon, Icon } = TONES[tone];
  return (
    <div
      role={tone === "fail" || tone === "warn" ? "alert" : "status"}
      className={cn("flex items-start gap-3 rounded-lg border p-3", box)}
    >
      <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", icon)} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{title}</p>
        {body ? <div className="mt-0.5 text-sm text-fg-muted">{body}</div> : null}
        {actions ? <div className="mt-2 flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
