"use client";

import { useMemo, type ReactNode } from "react";
import { diffLines } from "diff";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { highlightCode } from "@/lib/verilog/highlight";

export type CodeDiffProps = {
  before: string;
  after: string;
  beforeLabel: string;
  afterLabel: string;
  language?: "verilog" | "text";
};

type Row = { kind: "add" | "del" | "same"; oldNo: number | null; newNo: number | null; nodes: ReactNode[] };

function normalize(text: string): string {
  return text.length === 0 || text.endsWith("\n") ? text : `${text}\n`;
}

function buildRows(before: string, after: string, language: "verilog" | "text") {
  // Highlight each side as a whole so block comments stay correct; removed rows use the before side.
  const beforeNodes = highlightCode(before.replace(/\n$/, ""), language);
  const afterNodes = highlightCode(after.replace(/\n$/, ""), language);
  const rows: Row[] = [];
  let oldNo = 1;
  let newNo = 1;
  let added = 0;
  let removed = 0;
  for (const part of diffLines(normalize(before), normalize(after))) {
    const count = part.count ?? part.value.split("\n").length - 1;
    for (let i = 0; i < count; i++) {
      if (part.added) {
        rows.push({ kind: "add", oldNo: null, newNo, nodes: afterNodes[newNo - 1] ?? [] });
        newNo++;
        added++;
      } else if (part.removed) {
        rows.push({ kind: "del", oldNo, newNo: null, nodes: beforeNodes[oldNo - 1] ?? [] });
        oldNo++;
        removed++;
      } else {
        rows.push({ kind: "same", oldNo, newNo, nodes: afterNodes[newNo - 1] ?? [] });
        oldNo++;
        newNo++;
      }
    }
  }
  return { rows, added, removed };
}

/** Unified line diff (datasheet style). Added and removed rows carry a + or - gutter symbol as well as a fill, never color alone. */
export function CodeDiff({ before, after, beforeLabel, afterLabel, language = "verilog" }: CodeDiffProps) {
  const { rows, added, removed } = useMemo(() => buildRows(before, after, language), [before, after, language]);
  const unchanged = added === 0 && removed === 0;

  return (
    <div className="codebox">
      <div className="codebox-h">
        <span className="codebox-f">
          <span>{beforeLabel}</span>
          <ArrowRight aria-hidden className="size-3" />
          <span>{afterLabel}</span>
        </span>
        <span className="mono codebox-n">
          <span className="add">+{added}</span> <span className="del">-{removed}</span>
        </span>
      </div>
      {unchanged ? (
        <p className="dp-empty">No changes</p>
      ) : (
        <div
          role="region"
          tabIndex={0}
          aria-label={`Changes from ${beforeLabel} to ${afterLabel}`}
          className="codebox-b"
        >
          <pre className="codebox-pre">
            <code>
              {rows.map((row, i) => (
                <div
                  key={i}
                  className={cn(
                    "flex min-h-5",
                    row.kind === "add" && "bg-diff-add",
                    row.kind === "del" && "bg-diff-del",
                  )}
                >
                  <span aria-hidden className="ln">
                    {row.oldNo ?? ""}
                  </span>
                  <span aria-hidden className="ln">
                    {row.newNo ?? ""}
                  </span>
                  <span
                    className={cn(
                      "gut",
                      row.kind === "add" && "add",
                      row.kind === "del" && "del",
                    )}
                  >
                    {row.kind === "add" ? "+" : row.kind === "del" ? "-" : " "}
                    {row.kind === "same" ? null : (
                      <span className="sr-only">{row.kind === "add" ? "added" : "removed"}</span>
                    )}
                  </span>
                  <span className="pr-4 whitespace-pre text-syn-plain">{row.nodes}</span>
                </div>
              ))}
            </code>
          </pre>
        </div>
      )}
    </div>
  );
}
