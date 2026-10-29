"use client";

import { useSyncExternalStore } from "react";
import type { KnobValue } from "@/lib/contracts/presets";
import type { Priority } from "@/lib/contracts/types";
import { readSteps, type WalkStep } from "@/lib/contracts/ask";
import { ResearchSchema, VERDICTS, type Research, type Verdict } from "@/lib/contracts/research";

/** A live run started from this browser: enough to list it on the bench and rebuild its chat after a reload. */
export type RecentRun = {
  id: string;
  title: string;
  /** The sentence the user sent; empty when the chip was picked by hand. */
  prompt: string;
  familyId: string;
  knobs: Record<string, KnobValue>;
  priority: Priority;
  /** The knobs were sent to the agent (its "params" feature), so a rerun sends them again. */
  sendKnobs: boolean;
  startedAt: number;
  /** How the run ended, once this browser has seen it end. Absent while it is going or was never seen to finish. */
  ended?: RunEnd;
  /** The questions asked about this run after it ended, with Verity's answers, so a reload shows the same conversation. */
  asks?: AskStored[];
  /** The fact check on the first message (verdict and research), so a reload shows the same first reply. */
  check?: StoredCheck;
  /** The knob ids the first message did not state, so they took their defaults ("I assumed: ..."). */
  assumed?: string[];
  /** The chat thread the run started from (the agent's "agent" feature), so opening the run restores that thread. Absent for older runs. */
  threadId?: string;
};

/** The verdict (set by the server's code) and the research behind the first reply, as kept in this browser. */
export type StoredCheck = { verdict: Verdict | null; research: Research | null };

export type RunEnd = "done" | "stopped" | "failed";

/** A new run Verity proposed after a question: the chip, the goal, and what the last run cost. Nothing runs until Confirm. */
export type AskProposal = {
  presetId: string;
  params: Record<string, KnobValue>;
  priority: Priority;
  mode?: string;
  title: string;
  idea: string | null;
  lastRunUsd: number | null;
  capUsd: number;
};

/** One turn of the conversation under a finished run, as kept in this browser. Waiting and failed turns are never kept. */
export type AskStored =
  /** `version`: the version attached to the question ("v4"), when one was. */
  | { id: string; kind: "user"; text: string; version?: string }
  /** `steps`: a walkthrough of the run, replayed from here without a new model call. */
  | { id: string; kind: "answer"; text: string; bullets: string[]; basis: string; steps?: WalkStep[] }
  | { id: string; kind: "proposal"; text: string; prompt: string; proposal: AskProposal; state: "open" | "dismissed" | "started"; newRunId?: string }
  | { id: string; kind: "new_chip"; text: string; prompt: string }
  | { id: string; kind: "refused"; text: string };

// localStorage, not sessionStorage: the list outlives the tab. It holds no tokens, only what the user typed and picked.
const KEY = "verity.recentRuns";
const EVENT = "verity:recent-runs";
const MAX_RUNS = 20;
const MAX_ASKS = 40;
const NONE: RecentRun[] = [];
export const PRIORITIES: ReadonlySet<string> = new Set(["area", "speed", "power"]);
const ENDS: ReadonlySet<string> = new Set(["done", "stopped", "failed"]);

let cache: { raw: string | null; runs: RecentRun[] } = { raw: null, runs: NONE };

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function isKnobs(x: unknown): x is Record<string, KnobValue> {
  if (typeof x !== "object" || x === null || Array.isArray(x)) return false;
  return Object.values(x).every((v) => typeof v === "number" || typeof v === "boolean" || typeof v === "string");
}

export const isStr = (x: unknown): x is string => typeof x === "string";

export function isProposal(x: unknown): x is AskProposal {
  if (typeof x !== "object" || x === null) return false;
  const p = x as Partial<AskProposal>;
  return (
    isStr(p.presetId) &&
    isKnobs(p.params) &&
    typeof p.priority === "string" &&
    PRIORITIES.has(p.priority) &&
    isStr(p.title) &&
    (p.mode === undefined || isStr(p.mode)) &&
    (p.idea === null || isStr(p.idea)) &&
    (p.lastRunUsd === null || typeof p.lastRunUsd === "number") &&
    typeof p.capUsd === "number"
  );
}

export function isAsk(x: unknown): x is AskStored {
  if (typeof x !== "object" || x === null) return false;
  const t = x as Record<string, unknown>;
  if (!isStr(t.id) || t.id === "" || !isStr(t.text)) return false;
  switch (t.kind) {
    case "user":
      return t.version === undefined || (isStr(t.version) && /^v\d+$/.test(t.version));
    case "refused":
      return true;
    case "answer":
      return Array.isArray(t.bullets) && t.bullets.every(isStr) && isStr(t.basis) && (t.steps === undefined || (Array.isArray(t.steps) && readSteps(t.steps)?.length === t.steps.length));
    case "proposal":
      return isStr(t.prompt) && isProposal(t.proposal) && (t.state === "open" || t.state === "dismissed" || t.state === "started") && (t.newRunId === undefined || isStr(t.newRunId));
    case "new_chip":
      return isStr(t.prompt);
    default:
      return false;
  }
}

const VERDICT_SET: ReadonlySet<string> = new Set(VERDICTS);

/** A saved check that no longer reads is dropped on its own; the run stays in the list. */
export function readCheck(x: unknown): StoredCheck | undefined {
  if (typeof x !== "object" || x === null) return undefined;
  const c = x as Record<string, unknown>;
  const verdict = isStr(c.verdict) && VERDICT_SET.has(c.verdict) ? (c.verdict as Verdict) : null;
  const parsed = c.research === null || c.research === undefined ? null : ResearchSchema.safeParse(c.research);
  const research = parsed?.success ? parsed.data : null;
  return verdict || research ? { verdict, research } : undefined;
}

export function readAssumed(x: unknown): string[] | undefined {
  return Array.isArray(x) && x.every(isStr) ? x : undefined;
}

/** A damaged turn is dropped on its own; it never takes the run out of the list. */
function readAsks(x: unknown): AskStored[] | undefined {
  if (!Array.isArray(x)) return undefined;
  const turns = x.filter(isAsk).slice(-MAX_ASKS);
  return turns.length > 0 ? turns : undefined;
}

function isRecentRun(x: unknown): x is RecentRun {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Partial<RecentRun>;
  return (
    typeof r.id === "string" &&
    r.id !== "" &&
    typeof r.title === "string" &&
    typeof r.prompt === "string" &&
    typeof r.familyId === "string" &&
    isKnobs(r.knobs) &&
    typeof r.priority === "string" &&
    PRIORITIES.has(r.priority) &&
    typeof r.sendKnobs === "boolean" &&
    typeof r.startedAt === "number" &&
    (r.ended === undefined || (typeof r.ended === "string" && ENDS.has(r.ended)))
  );
}

/** The stored list, newest first. Cached on the raw string so useSyncExternalStore sees a stable value. */
function read(): RecentRun[] {
  const raw = storage()?.getItem(KEY) ?? null;
  if (raw === cache.raw) return cache.runs;
  let runs = NONE;
  if (raw) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        runs = parsed
          .filter(isRecentRun)
          .slice(0, MAX_RUNS)
          .map((r) => ({ ...r, asks: readAsks(r.asks), check: readCheck(r.check), assumed: readAssumed(r.assumed), threadId: isStr(r.threadId) && r.threadId !== "" ? r.threadId : undefined }));
      }
    } catch {
      // a malformed value reads as an empty list
    }
  }
  cache = { raw, runs };
  return runs;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange); // another tab started a run
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Runs started in this browser, newest first. Empty on the server and before the browser store is read. */
export function useRecentRuns(): RecentRun[] {
  return useSyncExternalStore(subscribe, read, () => NONE);
}

/** Puts a run at the top of the list (replacing an older entry with the same id) and tells subscribers. */
export function recordRun(run: RecentRun): void {
  const store = storage();
  if (!store) return;
  const next = [run, ...read().filter((r) => r.id !== run.id)].slice(0, MAX_RUNS);
  try {
    store.setItem(KEY, JSON.stringify(next));
  } catch {
    return; // storage full or blocked: the run still works, it is just not listed
  }
  window.dispatchEvent(new Event(EVENT));
}

/** Empties the list of runs started in this browser (done at sign-out, with the kept chats) and tells subscribers. */
export function clearRecentRuns(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(KEY);
  } catch {
    return;
  }
  window.dispatchEvent(new Event(EVENT));
}

/** Notes how a listed run ended, in place: the order of the list does not change. Nothing is written when it is already noted. */
export function markEnded(id: string, ended: RunEnd): void {
  const store = storage();
  if (!store) return;
  const runs = read();
  const at = runs.findIndex((r) => r.id === id);
  if (at < 0 || runs[at].ended === ended) return;
  const next = runs.map((r, i) => (i === at ? { ...r, ended } : r));
  try {
    store.setItem(KEY, JSON.stringify(next));
  } catch {
    return;
  }
  window.dispatchEvent(new Event(EVENT));
}

/** The saved conversation under a run, or none. */
export function storedAsks(id: string): AskStored[] {
  return read().find((r) => r.id === id)?.asks ?? [];
}

/** Saves the conversation under a listed run, in place. Nothing is written for a run this browser did not list, or when nothing changed. */
export function saveAsks(id: string, asks: AskStored[]): void {
  const store = storage();
  if (!store) return;
  const runs = read();
  const at = runs.findIndex((r) => r.id === id);
  if (at < 0) return;
  const kept = asks.slice(-MAX_ASKS);
  if (JSON.stringify(runs[at].asks ?? []) === JSON.stringify(kept)) return;
  const next = runs.map((r, i) => (i === at ? { ...r, asks: kept.length > 0 ? kept : undefined } : r));
  try {
    store.setItem(KEY, JSON.stringify(next));
  } catch {
    return; // storage full or blocked: the answers still show until the page is closed
  }
  window.dispatchEvent(new Event(EVENT));
}
