import type { KnobValue } from "@/lib/contracts/presets";
import type { Priority } from "@/lib/contracts/types";
import { CpuActionSchema, CpuKindsSchema, type CpuAction, type CpuKind } from "@/lib/contracts/cpu";
import { readToolSteps, type ToolStep } from "@/lib/contracts/agent";
import { ResearchSchema, type Research } from "@/lib/contracts/research";
import { PRIORITIES, isAsk, isKnobs, isStr, readAssumed, readCheck, type AskStored, type StoredCheck } from "./use-recent-runs";

/** A chip the bench can build: a family and knobs that are all inside the family's ranges. */
export type Chip = { familyId: string; knobs: Record<string, KnobValue>; title: string };

/**
 * One message of a chat thread as kept in this browser (`verity.threads`). The thread is the conversation with the chip
 * engineer agent: what the reader typed, the first fact check, the agent's replies, and a marker where each run started.
 * A waiting line and a failed reply are never kept (see ChatMessage in the bench's chat.ts).
 */
export type ThreadMessage =
  | { id: string; kind: "cpu_choice"; cpuKinds: CpuKind[]; jobId?: string }
  | { id: string; kind: "user"; text: string; version?: string }
  | {
      id: string;
      kind: "card";
      /** The sentence that led to this card, kept for the "Your runs" list. */
      prompt: string;
      intro: string;
      note?: string;
      familyId: string;
      knobs: Record<string, KnobValue>;
      priority: Priority;
      /** Run was pressed: the card is a read-only record of what was sent. */
      sent: boolean;
      check?: StoredCheck;
      /** The knob ids the sentence did not state, so they took their defaults. Absent when the agent does not say. */
      assumed?: string[];
      /** Present when the agent proposed this chip (a proposal before any run). */
      toolSteps?: ToolStep[];
    }
  | { id: string; kind: "refused"; prompt: string; reason: string; closest: Chip | null; check?: StoredCheck }
  /** An agent answer. `walkRunId`: the run its walkthrough (`steps`) is about. */
  | (Extract<AskStored, { kind: "answer" }> & { toolSteps?: ToolStep[]; chips?: string[]; research?: Research | null; walkRunId?: string })
  /** An agent proposal after a run: the same turn as under a run, with Confirm. */
  | (Extract<AskStored, { kind: "proposal" }> & { toolSteps?: ToolStep[] })
  /**
   * The agent proposes a CPU job (new, core retry or upgrade). Only Confirm calls the API. `restored`: the card came from an
   * earlier load of the chat, so it is never actionable (ask the agent again for a fresh one). `jobId`: the job it started.
   */
  | { id: string; kind: "cpu_proposal"; text: string; action: CpuAction; state: "open" | "dismissed" | "started"; jobId?: string; restored?: boolean; toolSteps?: ToolStep[] }
  /** The agent said no, in a sentence. */
  | { id: string; kind: "declined"; text: string; toolSteps?: ToolStep[] }
  /** A run started here. The run lines (progress, the end) show at this point for the run in the URL. */
  | { id: string; kind: "run"; runId: string };

export type StoredThread = { id: string; updatedAt: number; messages: ThreadMessage[] };

// localStorage, not sessionStorage: a reload, or a new tab, finds the conversation again. It holds no tokens, only what
// the reader typed and what Verity answered.
const KEY = "verity.threads";
const OPEN_KEY = "verity.openThread";
const MAX_THREADS = 20;
const MAX_MESSAGES = 60;
const NONE: StoredThread[] = [];

let cache: { raw: string | null; threads: StoredThread[] } = { raw: null, threads: NONE };

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function readClosest(x: unknown): Chip | null | undefined {
  if (x === null) return null;
  if (typeof x !== "object" || x === undefined) return undefined;
  const c = x as Record<string, unknown>;
  return isStr(c.familyId) && isKnobs(c.knobs) && isStr(c.title) ? { familyId: c.familyId, knobs: c.knobs, title: c.title } : undefined;
}

/** A message that reads, normalised; null for one that does not. A damaged message is dropped on its own, never the thread. */
function readMessage(x: unknown): ThreadMessage | null {
  if (typeof x !== "object" || x === null) return null;
  const t = x as Record<string, unknown>;
  const id = t.id;
  if (!isStr(id) || id === "") return null;
  const toolSteps = readToolSteps(t.toolSteps);
  const steps = toolSteps.length > 0 ? { toolSteps } : {};
  switch (t.kind) {
    case "cpu_choice": {
      const kinds = CpuKindsSchema.safeParse(t.cpuKinds);
      if (!kinds.success || (t.jobId !== undefined && !isStr(t.jobId))) return null;
      return { id, kind: "cpu_choice", cpuKinds: kinds.data, ...(isStr(t.jobId) && t.jobId ? { jobId: t.jobId } : {}) };
    }
    case "user": {
      const { text, version } = t;
      if (!isStr(text) || (version !== undefined && !(isStr(version) && /^v\d+$/.test(version)))) return null;
      return version === undefined ? { id, kind: "user", text } : { id, kind: "user", text, version: version as string };
    }
    case "card": {
      const { prompt, intro, note, familyId, knobs, priority, sent } = t;
      if (!isStr(prompt) || !isStr(intro) || !(note === undefined || isStr(note)) || !isStr(familyId) || !isKnobs(knobs)) return null;
      if (!isStr(priority) || !PRIORITIES.has(priority) || typeof sent !== "boolean") return null;
      const check = readCheck(t.check);
      const assumed = readAssumed(t.assumed);
      return {
        id,
        kind: "card",
        prompt,
        intro,
        ...(note ? { note } : {}),
        familyId,
        knobs,
        priority: priority as Priority,
        sent,
        ...(check ? { check } : {}),
        ...(assumed ? { assumed } : {}),
        ...steps,
      };
    }
    case "refused": {
      const { prompt, reason } = t;
      const closest = readClosest(t.closest);
      if (!isStr(prompt) || !isStr(reason) || closest === undefined) return null;
      const check = readCheck(t.check);
      return { id, kind: "refused", prompt, reason, closest, ...(check ? { check } : {}) };
    }
    case "answer": {
      if (!isAsk(x) || x.kind !== "answer") return null;
      const chips = Array.isArray(t.chips) && t.chips.every(isStr) && t.chips.length > 0 ? t.chips : undefined;
      const research = t.research === null || t.research === undefined ? undefined : ResearchSchema.safeParse(t.research);
      const walkRunId = isStr(t.walkRunId) && t.walkRunId !== "" ? t.walkRunId : undefined;
      return {
        id,
        kind: "answer",
        text: x.text,
        bullets: x.bullets,
        basis: x.basis,
        ...(x.steps ? { steps: x.steps } : {}),
        ...steps,
        ...(chips ? { chips } : {}),
        ...(research?.success ? { research: research.data } : {}),
        ...(walkRunId ? { walkRunId } : {}),
      };
    }
    case "proposal": {
      if (!isAsk(x) || x.kind !== "proposal") return null;
      return { id, kind: "proposal", text: x.text, prompt: x.prompt, proposal: x.proposal, state: x.state, ...(x.newRunId ? { newRunId: x.newRunId } : {}), ...steps };
    }
    case "cpu_proposal": {
      const action = CpuActionSchema.safeParse(t.action);
      if (!isStr(t.text) || !action.success || !(t.state === "open" || t.state === "dismissed" || t.state === "started")) return null;
      // A card kept in this browser and still open is from an earlier load: it never asks for Confirm again.
      const restored = t.restored === true || t.state === "open";
      return { id, kind: "cpu_proposal", text: t.text, action: action.data, state: t.state, ...(isStr(t.jobId) && t.jobId ? { jobId: t.jobId } : {}), ...(restored ? { restored: true } : {}), ...steps };
    }
    case "declined":
      return isStr(t.text) ? { id, kind: "declined", text: t.text, ...steps } : null;
    case "run":
      return isStr(t.runId) && t.runId !== "" ? { id, kind: "run", runId: t.runId } : null;
    default:
      return null;
  }
}

function readThreadRecord(x: unknown): StoredThread | null {
  if (typeof x !== "object" || x === null) return null;
  const t = x as Record<string, unknown>;
  if (!isStr(t.id) || t.id === "" || typeof t.updatedAt !== "number" || !Array.isArray(t.messages)) return null;
  const messages = t.messages.flatMap((m) => {
    const read = readMessage(m);
    return read ? [read] : [];
  });
  return messages.length > 0 ? { id: t.id, updatedAt: t.updatedAt, messages: messages.slice(-MAX_MESSAGES) } : null;
}

/** The stored threads, newest first. Cached on the raw string so a repeated read does not parse again. */
function readAll(): StoredThread[] {
  const raw = storage()?.getItem(KEY) ?? null;
  if (raw === cache.raw) return cache.threads;
  let threads = NONE;
  if (raw) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        threads = parsed.flatMap((x) => {
          const t = readThreadRecord(x);
          return t ? [t] : [];
        }).slice(0, MAX_THREADS);
      }
    } catch {
      // a malformed value reads as no threads
    }
  }
  cache = { raw, threads };
  return threads;
}

/** A thread kept in this browser, or nothing. */
export function readThread(id: string): StoredThread | undefined {
  return readAll().find((t) => t.id === id);
}

/** Keeps the thread at the top of the list, with its newest 60 messages. Nothing is written when it did not change or has nothing to keep. */
export function saveThread(id: string, messages: ThreadMessage[]): void {
  const store = storage();
  if (!store) return;
  const kept = messages.slice(-MAX_MESSAGES);
  if (kept.length === 0) return;
  const all = readAll();
  const old = all.find((t) => t.id === id);
  if (old && JSON.stringify(old.messages) === JSON.stringify(kept)) return;
  let next = [{ id, updatedAt: Date.now(), messages: kept }, ...all.filter((t) => t.id !== id)].slice(0, MAX_THREADS);
  // Storage full: let the oldest threads go until it fits. The open thread is the newest, so it is the last to go.
  for (;;) {
    try {
      store.setItem(KEY, JSON.stringify(next));
      return;
    } catch {
      if (next.length <= 1) return; // blocked: the thread still works on screen, it is just not kept
      next = next.slice(0, -1);
    }
  }
}

/** Forgets every chat kept in this browser and which one was open. Done at sign-out, so the next person on this browser does not find them. */
export function clearThreads(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(KEY);
    store.removeItem(OPEN_KEY);
  } catch {
    // blocked: nothing was kept to forget
  }
}

/** The thread that was open when the page was last used, or none. */
export function readOpenThread(): string | null {
  const id = storage()?.getItem(OPEN_KEY) ?? null;
  return id ? id : null;
}

/** Notes which thread is open (none after New chip), so a reload restores it, also before any run. */
export function setOpenThread(id: string | null): void {
  const store = storage();
  if (!store) return;
  try {
    if (id) store.setItem(OPEN_KEY, id);
    else store.removeItem(OPEN_KEY);
  } catch {
    // blocked: the next load starts empty
  }
}
