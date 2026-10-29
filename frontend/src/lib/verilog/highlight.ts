import { createElement, type ReactNode } from "react";

export type CodeLanguage = "verilog" | "python" | "text";

/**
 * A small Verilog tokenizer that returns React nodes (never HTML strings, so no raw HTML injection).
 * Colors come from the syntax tokens in globals.css. Python and text render plain.
 */

const KEYWORDS = new Set([
  "module", "endmodule", "assign", "always", "always_ff", "always_comb", "begin", "end", "if", "else",
  "case", "endcase", "default", "posedge", "negedge", "parameter", "localparam", "generate", "endgenerate", "for",
]);

// Types and port directions share the type color.
const TYPES = new Set([
  "logic", "wire", "reg", "integer", "signed", "unsigned", "input", "output", "inout",
]);

const CLASS = {
  keyword: "text-syn-keyword",
  type: "text-syn-type",
  number: "text-syn-number",
  comment: "text-syn-comment",
  string: "text-syn-string",
  punct: "text-syn-punct",
} as const;

type Kind = keyof typeof CLASS;

// Groups: 1 line comment, 2 block comment start, 3 string, 4 sized number (32'h0, 8'b1010_0101),
// 5 plain number, 6 word (identifier, `directive or $system task), 7 punctuation and operators.
const TOKEN_RE =
  /(\/\/.*)|(\/\*)|("(?:\\.|[^"\\])*"?)|(\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+)|(\b\d[\d_]*\b)|(`?[A-Za-z_$][A-Za-z0-9_$]*)|((?:[^\s\w$"'`/]|\/(?![/*]))+)/g;

function span(kind: Kind, text: string, key: number): ReactNode {
  return createElement("span", { key, className: CLASS[kind] }, text);
}

function classifyWord(word: string): Kind | null {
  if (word.startsWith("$")) return "string";
  if (word.startsWith("`")) return "keyword";
  if (KEYWORDS.has(word)) return "keyword";
  if (TYPES.has(word)) return "type";
  return null;
}

type LineResult = { nodes: ReactNode[]; inBlock: boolean };

/** Tokenizes one line. `inBlock` says whether the line starts inside a multi-line block comment. */
function tokenizeLine(line: string, inBlock: boolean): LineResult {
  const nodes: ReactNode[] = [];
  let key = 0;
  let pos = 0;

  if (inBlock) {
    const close = line.indexOf("*/");
    if (close === -1) return { nodes: line ? [span("comment", line, key)] : [], inBlock: true };
    nodes.push(span("comment", line.slice(0, close + 2), key++));
    pos = close + 2;
  }

  const re = new RegExp(TOKEN_RE.source, "g");
  re.lastIndex = pos;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) {
    if (m.index > pos) nodes.push(line.slice(pos, m.index));
    const text = m[0];
    pos = m.index + text.length;

    if (m[1] !== undefined) {
      nodes.push(span("comment", text, key++));
    } else if (m[2] !== undefined) {
      const close = line.indexOf("*/", m.index + 2);
      if (close === -1) {
        nodes.push(span("comment", line.slice(m.index), key++));
        return { nodes, inBlock: true };
      }
      nodes.push(span("comment", line.slice(m.index, close + 2), key++));
      pos = close + 2;
      re.lastIndex = pos;
    } else if (m[3] !== undefined) {
      nodes.push(span("string", text, key++));
    } else if (m[4] !== undefined || m[5] !== undefined) {
      nodes.push(span("number", text, key++));
    } else if (m[6] !== undefined) {
      const kind = classifyWord(text);
      nodes.push(kind ? span(kind, text, key++) : text);
    } else {
      nodes.push(span("punct", text, key++));
    }
  }
  if (pos < line.length) nodes.push(line.slice(pos));
  return { nodes, inBlock: false };
}

/** Highlights one line on its own (block comments that started on an earlier line are not known here). */
export function highlightLine(line: string, language: CodeLanguage): ReactNode[] {
  if (language !== "verilog") return [line];
  return tokenizeLine(line, false).nodes;
}

/** Highlights a whole source text, one node array per line, carrying block comment state across lines. */
export function highlightCode(code: string, language: CodeLanguage): ReactNode[][] {
  const lines = code.split("\n");
  if (language !== "verilog") return lines.map((line) => [line]);
  let inBlock = false;
  return lines.map((line) => {
    const result = tokenizeLine(line, inBlock);
    inBlock = result.inBlock;
    return result.nodes;
  });
}
