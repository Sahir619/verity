"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { highlightCode } from "@/lib/verilog/highlight";

export type CodeViewProps = {
  code: string;
  language: "verilog" | "python" | "text";
  filename?: string;
  highlightLines?: number[];
  maxHeight?: string;
  copyable?: boolean;
};

/** Read-only code with line numbers and Verilog highlighting (datasheet style). No HTML strings: highlighting returns React nodes. */
export function CodeView({ code, language, filename, highlightLines, maxHeight, copyable = true }: CodeViewProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const lines = useMemo(
    () => highlightCode(code.endsWith("\n") ? code.slice(0, -1) : code, language),
    [code, language],
  );
  const marked = useMemo(() => new Set(highlightLines ?? []), [highlightLines]);
  const empty = code.length === 0;

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="codebox">
      {filename || copyable ? (
        <div className="codebox-h">
          <span className="mono codebox-f">{filename ?? ""}</span>
          {copyable && !empty ? (
            <button type="button" className="btn ghost xs" onClick={copy} aria-label={copied ? "Copied" : "Copy code"}>
              {copied ? <Check aria-hidden className="ico" /> : <Copy aria-hidden className="ico" />}
              <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
            </button>
          ) : null}
        </div>
      ) : null}
      {empty ? (
        <p className="dp-empty">No code yet.</p>
      ) : (
        <div
          role="region"
          tabIndex={0}
          aria-label={filename ? `Code: ${filename}` : "Code"}
          className="codebox-b"
          style={maxHeight ? { maxHeight } : undefined}
        >
          <pre className="codebox-pre">
            <code>
              {lines.map((nodes, i) => (
                <div key={i} className={cn("flex min-h-5", marked.has(i + 1) && "bg-surface-2")}>
                  <span aria-hidden className="ln ln-w">
                    {i + 1}
                  </span>
                  <span className="pr-4 whitespace-pre text-syn-plain">{nodes}</span>
                </div>
              ))}
            </code>
          </pre>
        </div>
      )}
      {empty ? null : (
        <div className="codebox-foot">
          {lines.length} {lines.length === 1 ? "line" : "lines"}
        </div>
      )}
    </div>
  );
}
