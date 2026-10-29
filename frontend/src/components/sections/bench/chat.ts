import { FAMILIES, familyById, knobDefaults, knobError, sanitizeKnobs, type KnobValue } from "@/lib/contracts/presets";
import type { Candidate, PPA, Priority, RunState } from "@/lib/contracts/types";
import type { LiveHealth } from "@/lib/contracts/view-types";
import { formatDelta, formatUsd } from "@/lib/format";
import type { AskProposal, AskStored, RecentRun, StoredCheck } from "@/lib/hooks/use-recent-runs";
import type { Chip, ThreadMessage } from "@/lib/hooks/use-threads";
import type { AskResult, MessageResult, ParseReply, ParseResult, Research, ThreadTurn, Verdict } from "@/lib/sources/live";
import { TERMINAL } from "./derive";

export type { Chip };

/** The chat box limit, the same as POST /parse accepts. */
export const MAX_CHARS = 300;

/**
 * The fact check on a first message: the verdict (set by the server's code, never by the model) and the research behind
 * it. Both are absent from an older agent, and then the reply reads exactly as it did before.
 */
export type Check = StoredCheck;

/**
 * One message in the thread. What is kept in this browser is `ThreadMessage` (the reader's messages, the fact check, the
 * agent's replies, a marker where a run started); the two kinds below are shown and never kept. The run's progress and
 * its end are not messages: the thread reads them from the run state of the run in the URL, so a reload shows the same
 * thing. Nothing here goes to the backend.
 */
export type ChatMessage =
  | ThreadMessage
  /** `checking`: this agent also looks up sources, so the wait is longer and says so. `thinking`: a later message, answered by the agent. */
  | { id: string; kind: "parsing"; checking?: boolean; thinking?: boolean }
  /** `plain`: a failed question inside a thread, not a first message: no offer to pick a chip by hand. */
  | { id: string; kind: "error"; prompt: string; text: string; needsCode?: boolean; spendLimit?: boolean; plain?: boolean };

/** A turn under a finished run: what Verity kept in this browser, plus the two that are never kept (waiting, failed). */
export type AskTurn = AskStored | { id: string; kind: "asking" } | { id: string; kind: "error"; text: string; prompt: string; needsCode?: boolean };

// The time prefix keeps ids unique even if this module is loaded again while messages are on screen (dev hot reload).
const ID_PREFIX = Date.now().toString(36);
let seq = 0;
export function messageId(): string {
  seq += 1;
  return `m${ID_PREFIX}-${seq}`;
}

/** Family and knobs from an agent reply, only when every knob is in the bench's own table. */
export function checked(familyId: string, params: Record<string, unknown>): Chip | null {
  const f = familyById(familyId);
  if (!f) return null;
  const knobs: Record<string, KnobValue> = {};
  for (const k of f.knobs) {
    const v = (params[k.id] ?? k.default) as KnobValue;
    if (knobError(k, v) !== null) return null;
    knobs[k.id] = v;
  }
  return { familyId: f.id, knobs, title: f.describe(knobs) };
}

const FAMILY_ID = new RegExp(`\\b(${FAMILIES.map((f) => f.id).join("|")})\\b`, "g");

/** The model may name a family by its id ("mac4"); people read its name ("MAC array"). */
export function humanize(text: string): string {
  return text.replace(FAMILY_ID, (id) => familyById(id)?.name ?? id);
}

const OFFLINE = "The agent did not respond.";

/** The start error in the bench's own words: the store's "The agent did not respond." reads "Verity's server did not answer." */
export function plainStartError(error: string): string {
  return error === OFFLINE ? "Verity's server did not answer." : error;
}

/**
 * The agent's latest line for the progress message. Its plan line invites the user to "steer"; this chat has no steer
 * control, so that clause becomes a plain statement that building starts on its own.
 */
export function plainCaption(text: string): string {
  return text.replace(/\s*Building in \d+ s unless you steer\.?/i, " Building starts in a few seconds.");
}

/** Verity's reply to one sentence, from the POST /parse result. Every way out leaves the knobs reachable. */
export function replyTo(prompt: string, res: ParseResult): ChatMessage {
  const id = messageId();
  if (!res.ok) {
    if (res.reason === "unauthorized") {
      return { id, kind: "error", prompt, needsCode: true, text: "Live runs need an access code. Enter it and I will read your description again." };
    }
    if (res.reason === "rate_limited") return { id, kind: "error", prompt, text: res.error };
    // The spend limit refuses Run as well: picking the chip yourself would end at the same sentence.
    if (res.reason === "spend_limit") return { id, kind: "error", prompt, text: res.error, spendLimit: true };
    if (res.reason === "unavailable") return noParse(prompt);
    return {
      id,
      kind: "error",
      prompt,
      text: res.error === OFFLINE ? "I could not reach Verity's server, so I could not read that." : "I could not read that just now.",
    };
  }
  const reply = res.reply;
  if (reply.verdict === "cpu_choice") {
    return reply.cpuKinds?.length
      ? { id, kind: "cpu_choice", cpuKinds: reply.cpuKinds }
      : { id, kind: "error", prompt, text: "The CPU choices could not be read. Try again." };
  }
  const check = checkOf(reply);
  if (reply.result === "in_range") {
    const chip = checked(reply.family, reply.params);
    if (!chip) return { id, kind: "error", prompt, text: "I read that, but the settings it asks for are outside what Verity can build." };
    const card: ChatMessage = { id, kind: "card", prompt, intro: "I can build this.", note: reply.note ? humanize(reply.note) : undefined, ...cardChip(chip), sent: false, check };
    const assumed = assumedOf(chip.familyId, reply.statedKnobs);
    return assumed ? { ...card, assumed } : card;
  }
  const closest = reply.closest ? checked(reply.closest.family, reply.closest.params) : null;
  return { id, kind: "refused", prompt, reason: humanize(reply.reason), closest, check };
}

/** The family's knobs the sentence did not state, from the agent's statedKnobs; nothing when the agent does not say. */
function assumedOf(familyId: string, stated: string[] | undefined): string[] | undefined {
  const f = familyById(familyId);
  if (!f || !stated) return undefined;
  const said = new Set(stated);
  return f.knobs.filter((k) => !said.has(k.id)).map((k) => k.id);
}

/**
 * "accumulate cycles 16", "enable input off", "pattern 1011": what Verity took for each knob nobody stated. A knob the
 * reader then changed on the card is no longer an assumption, so it is left out.
 */
export function assumedWords(familyId: string, knobs: Record<string, KnobValue>, ids: string[]): string[] {
  const f = familyById(familyId);
  if (!f) return [];
  return f.knobs
    .filter((k) => ids.includes(k.id) && knobs[k.id] === k.default)
    .map((k) => {
      const v = knobs[k.id];
      const name = k.label.toLowerCase();
      if (k.kind === "bool") return `${name} ${v === true ? "on" : "off"}`;
      if (k.kind === "bits") return `${name} ${String(v)}`;
      // "lanes 4", not "lanes 4 lanes": the unit is left out when the name already says it.
      return name.endsWith(k.unit.toLowerCase()) ? `${name} ${v}` : `${name} ${v} ${k.unit}`;
    });
}

function checkOf(reply: ParseReply): Check | undefined {
  const verdict = reply.verdict ?? null;
  const research = reply.research ?? null;
  return verdict || research ? { verdict, research } : undefined;
}

/** Each verdict is a shape and a word, never colour alone: a hollow, dashed, dotted, half or filled square (ink, not amber). */
export const VERDICT: Record<Verdict, { word: string; shape: "hollow" | "dashed" | "dot" | "half" | "full" | "plus" }> = {
  cpu_choice: { word: "Choose a CPU", shape: "plus" },
  not_a_chip: { word: "Not a chip", shape: "hollow" },
  not_buildable_here: { word: "Not buildable here", shape: "half" },
  buildable_now: { word: "Buildable now", shape: "full" },
  buildable_with_changes: { word: "Buildable with changes", shape: "dot" },
  could_not_check: { word: "Could not check", shape: "dashed" },
  custom_block: { word: "Custom block", shape: "plus" },
};

/** Whether the reply says its facts could not be checked (a "Try again" is offered, and a chip card is marked unchecked). */
export const unchecked = (check: Check | undefined): boolean => check?.verdict === "could_not_check";

/** How a source is described: a plain word for its tier, not the letter. The server's tiers: A papers, standards and preprints; B vendor, project and university pages; C open-edit pages; P patents. */
export const TIER_WORD: Record<"A" | "B" | "C" | "P", string> = {
  A: "paper or standard",
  B: "vendor, project or university",
  C: "open source or wiki",
  P: "patent",
};

/** A link from the web, only when it is a plain http or https address. Titles and domains are shown as text, never as markup. */
export function safeHref(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}

/** "Oct 22, 2026" for an ISO time; anything that is not a date is shown as it came, and an empty one as nothing. */
export function plainDate(text: string): string {
  const t = Date.parse(text);
  return Number.isFinite(t) ? new Date(t).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : text.trim();
}

/** Dollars with a third decimal while they are small, so a check that cost half a cent does not read as $0.01. */
export function formatSmallUsd(usd: number): string {
  return usd > 0 && usd < 0.1 ? `$${usd.toFixed(3)}` : formatUsd(usd);
}

/** Turns saved in this browser before the limit left the UI still say "Cap $0.50." or "of its $0.50 cap": drop that clause. */
export function withoutCap(text: string): string {
  return text.replace(/\s*Cap \$\d+(?:\.\d+)?\./g, "").replace(/ of its \$\d+(?:\.\d+)? cap\./g, ".");
}

/** The sentence ids a sentence cites, only those the research lists, each once and in order. The server drops the rest; so does this. */
export function citedSources(research: Research, sourceIds: number[]): Research["sources"] {
  const seen = new Set<number>();
  const out: Research["sources"] = [];
  for (const n of sourceIds) {
    const s = research.sources.find((x) => x.n === n);
    if (s && !seen.has(n)) {
      seen.add(n);
      out.push(s);
    }
  }
  return out;
}

/** The reply when this server cannot read sentences at all (no "parse" feature). */
export function noParse(prompt: string): ChatMessage {
  return { id: messageId(), kind: "error", prompt, text: "This server cannot read descriptions yet." };
}

function cardChip(chip: Chip): { familyId: string; knobs: Record<string, KnobValue>; priority: Priority } {
  return { familyId: chip.familyId, knobs: chip.knobs, priority: "area" };
}

/** A card for a chip the user accepted ("Use the closest chip"). */
export function closestCard(prompt: string, chip: Chip): ChatMessage {
  return { id: messageId(), kind: "card", prompt, intro: "Here is the closest chip I can build.", ...cardChip(chip), sent: false };
}

/** A card at the first family's defaults ("Pick it yourself"). */
export function pickCard(prompt: string): ChatMessage {
  const f = FAMILIES[0];
  return {
    id: messageId(),
    kind: "card",
    prompt,
    intro: "Pick the chip and its settings, then press Run.",
    familyId: f.id,
    knobs: knobDefaults(f),
    priority: "area",
    sent: false,
  };
}

/** The start of a thread rebuilt from the "Your runs" list after a reload: what was asked, and what was sent. */
export function threadOf(entry: RecentRun | undefined): ChatMessage[] {
  if (!entry) return [];
  const f = familyById(entry.familyId);
  if (!f) return entry.prompt ? [{ id: `${entry.id}:u`, kind: "user", text: entry.prompt }] : [];
  // The fact check and what was assumed come back with the run, so a reload shows the same first reply.
  const card: ChatMessage = {
    id: `${entry.id}:c`,
    kind: "card",
    prompt: entry.prompt,
    intro: "",
    familyId: f.id,
    knobs: sanitizeKnobs(f, entry.knobs),
    priority: entry.priority,
    sent: true,
    ...(entry.check ? { check: entry.check } : {}),
    ...(entry.assumed ? { assumed: entry.assumed } : {}),
  };
  return entry.prompt ? [{ id: `${entry.id}:u`, kind: "user", text: entry.prompt }, card] : [card];
}

// ---------- questions about a finished run ----------

/** The turn that answers one question, from the POST /runs/{id}/ask result. A proposal waits for Confirm. */
export function askTurnFor(prompt: string, res: AskResult): AskTurn {
  const id = messageId();
  if (!res.ok) return { id, kind: "error", prompt, text: res.error, needsCode: res.reason === "unauthorized" };
  const r = res.reply;
  switch (r.kind) {
    case "answer":
      return r.steps ? { id, kind: "answer", text: r.text, bullets: r.bullets, basis: r.basis, steps: r.steps } : { id, kind: "answer", text: r.text, bullets: r.bullets, basis: r.basis };
    case "proposal":
      return {
        id,
        kind: "proposal",
        text: r.text,
        prompt,
        proposal: { ...r.proposal, idea: r.proposal.idea ?? null, lastRunUsd: r.proposal.lastRunUsd ?? null },
        state: "open",
      };
    case "new_chip":
      return { id, kind: "new_chip", text: r.text, prompt };
    case "refused":
      return { id, kind: "refused", text: r.text };
  }
}

/** The turns worth keeping: a question and its reply. A waiting turn, a failed one, and the question that belonged to it are dropped. */
export function persistable(turns: AskTurn[]): AskStored[] {
  const out: AskStored[] = [];
  turns.forEach((t, i) => {
    if (t.kind === "asking" || t.kind === "error") return;
    const next = turns[i + 1];
    if (t.kind === "user" && (!next || next.kind === "asking" || next.kind === "error")) return;
    out.push(t);
  });
  return out;
}

/** The turns without one failed reply and the question just before it, so that question can be asked again. */
export function withoutAsked(turns: AskTurn[], errorId: string): AskTurn[] {
  const at = turns.findIndex((t) => t.id === errorId);
  if (at < 0) return turns;
  return turns.filter((_, i) => i !== at && !(i === at - 1 && turns[i].kind === "user"));
}

// ---------- the thread with the chip engineer agent ----------

/** A new thread id, one per thread. It must be a UUID. randomUUID needs a secure page, so a plain http page builds one from random bytes. */
export function newThreadId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Suggestion chips under one reply: each is a message the reader can send as it is, so none is empty or over the box limit. */
const MAX_CHIPS = 6;

/** The messages that come back for one sent message: the fact check when this message ran it, then the agent's reply. */
export function agentMessages(
  prompt: string,
  res: MessageResult,
  ctx: {
    /** The run on screen when the message was sent, which a walkthrough in the reply belongs to. */
    runId: string | null;
    /** This thread already has a run: a proposal then waits for Confirm; before any run it is a chip card with Run. */
    hasRun: boolean;
    /** The first message of the thread. */
    first: boolean;
    /** The reply is read back from the server: a CPU proposal in it is never actionable. */
    restored?: boolean;
  },
): ChatMessage[] {
  const id = messageId();
  if (!res.ok) {
    const plain = ctx.first ? {} : { plain: true };
    if (res.reason === "unauthorized") return [{ id, kind: "error", prompt, needsCode: true, text: "Live runs need an access code. Enter it and I will send that again.", ...plain }];
    return [{ id, kind: "error", prompt, text: res.error, ...(res.reason === "spend_limit" ? { spendLimit: true } : {}), ...plain }];
  }
  const out: ChatMessage[] = res.check ? [replyTo(prompt, { ok: true, reply: res.check })] : [];
  const r = res.reply;
  if (!r) return out;
  const toolSteps = r.toolSteps.length > 0 ? { toolSteps: r.toolSteps } : {};
  switch (r.kind) {
    case "answer": {
      const chips = (r.chips ?? []).map((c) => c.trim()).filter((c) => c !== "" && c.length <= MAX_CHARS).slice(0, MAX_CHIPS);
      out.push({
        id,
        kind: "answer",
        text: r.text,
        bullets: r.bullets,
        basis: r.basis,
        ...(r.steps ? { steps: r.steps } : {}),
        ...(r.steps && ctx.runId ? { walkRunId: ctx.runId } : {}),
        ...toolSteps,
        ...(chips.length > 0 ? { chips } : {}),
        ...(r.research ? { research: r.research } : {}),
      });
      return out;
    }
    case "proposal": {
      const proposal = { ...r.proposal, idea: r.proposal.idea ?? null, lastRunUsd: r.proposal.lastRunUsd ?? null };
      if (ctx.hasRun) {
        out.push({ id, kind: "proposal", text: r.text, prompt, proposal, state: "open", ...toolSteps });
        return out;
      }
      // Before any run the proposal is a chip card with Run, the same card a fact-checked sentence gets.
      const chip = checked(proposal.presetId, proposal.params);
      out.push(
        chip
          ? { id, kind: "card", prompt, intro: withoutCap(r.text), familyId: chip.familyId, knobs: chip.knobs, priority: proposal.priority, sent: false, ...toolSteps }
          : { id, kind: "declined", text: `${r.text} I could not use that suggestion: its settings are outside what Verity can build.`, ...toolSteps },
      );
      return out;
    }
    case "refused":
      out.push({ id, kind: "declined", text: r.text, ...toolSteps });
      return out;
    case "cpu_proposal":
      out.push({ id, kind: "cpu_proposal", text: r.text, action: r.action, state: "open", ...(ctx.restored ? { restored: true } : {}), ...toolSteps });
      return out;
  }
}

/** A turn saved under a run before the thread existed, as a message of the thread. */
function askAsMessage(t: AskStored): ThreadMessage {
  return t.kind === "new_chip" || t.kind === "refused" ? { id: t.id, kind: "declined", text: t.text } : t;
}

/**
 * The thread for a run that has no marker in it yet: the messages already on screen with a marker after them, or, for a run
 * this browser has no thread for (started before the agent, or its thread was not kept), what was asked and sent, a marker
 * for the run, and the questions saved under it. The first message to the agent carries them, so nothing on screen is lost.
 */
export function seedThread(entry: RecentRun | undefined, runId: string, shown: ChatMessage[]): ChatMessage[] {
  const marker: ChatMessage = { id: messageId(), kind: "run", runId };
  return shown.length > 0 ? [...shown, marker] : [...threadOf(entry), marker, ...(entry?.asks ?? []).map(askAsMessage)];
}

/**
 * The messages worth keeping: not a waiting line or a failed reply, and not a question that got no reply. A question is kept
 * when any message that is not a waiting line or a failure follows it before the next question.
 */
export function persistableMessages(messages: ChatMessage[]): ThreadMessage[] {
  const out: ThreadMessage[] = [];
  messages.forEach((m, i) => {
    if (m.kind === "parsing" || m.kind === "error") return;
    if (m.kind === "user") {
      let answered = false;
      for (let j = i + 1; j < messages.length && messages[j].kind !== "user"; j++) {
        if (messages[j].kind !== "parsing" && messages[j].kind !== "error") answered = true;
      }
      if (!answered) return;
    }
    out.push(m);
  });
  return out;
}

/** A run of a chat as the server lists it. `createdAt` is empty when only the chat knows the run. */
export type ThreadRun = { id: string; createdAt: string };

/**
 * A chat the server kept, as messages. Each reply is rebuilt from the reply and the check stored with it, the same way a live
 * answer is built; a turn stored without them (an older one) comes back as its text and its step line. A run joins the chat
 * where it started, by time (a run with no time goes at the end), and a chip card or proposal that a run followed was sent.
 */
export function threadFromServer(turns: ThreadTurn[], runs: ThreadRun[]): ThreadMessage[] {
  const time = (s: string) => Date.parse(s);
  const queue = [...runs.filter((r) => Number.isFinite(time(r.createdAt))).sort((a, b) => time(a.createdAt) - time(b.createdAt)), ...runs.filter((r) => !Number.isFinite(time(r.createdAt)))];
  const out: ThreadMessage[] = [];
  const addRun = (r: ThreadRun) => out.push({ id: messageId(), kind: "run", runId: r.id });
  const lastRun = (): string | null => {
    const m = out.findLast((x) => x.kind === "run");
    return m?.kind === "run" ? m.runId : null;
  };
  let sentence = "";
  let replies = 0;
  for (const turn of turns) {
    const when = time(turn.createdAt);
    while (queue.length > 0 && time(queue[0].createdAt) < when) addRun(queue.shift() as ThreadRun);
    if (turn.role === "user") {
      sentence = turn.text;
      out.push({ id: messageId(), kind: "user", text: turn.text });
      continue;
    }
    const first = replies++ === 0;
    if (turn.reply || turn.check) {
      const runId = lastRun();
      const built = agentMessages(sentence, { ok: true, check: turn.check ?? null, reply: turn.reply ?? null }, { runId, hasRun: runId !== null, first, restored: true });
      for (const m of built) if (m.kind !== "parsing" && m.kind !== "error") out.push(m);
    } else {
      out.push({ id: messageId(), kind: "answer", text: turn.text, bullets: [], basis: "", ...(turn.toolSteps.length > 0 ? { toolSteps: turn.toolSteps } : {}) });
    }
  }
  queue.forEach(addRun);
  out.forEach((m, i) => {
    if (m.kind !== "run") return;
    for (let j = i - 1; j >= 0 && out[j].kind !== "run"; j--) {
      const c = out[j];
      if (c.kind === "card" && !c.sent) {
        out[j] = { ...c, sent: true };
        break;
      }
      if (c.kind === "proposal" && c.state === "open") {
        out[j] = { ...c, state: "started", newRunId: m.runId };
        break;
      }
    }
  });
  return out;
}

/** The proposed chip, only when its family and every knob are inside the bench's own table (the same check as a parsed sentence). */
export function proposalChip(p: AskProposal): Chip | null {
  return checked(p.presetId, p.params);
}

/** The knobs of a chip in plain words, one line each: "Width: 10 bits", "Enable input: With enable". */
export function knobWords(chip: Chip): { label: string; value: string }[] {
  const f = familyById(chip.familyId);
  if (!f) return [];
  return f.knobs.map((k) => {
    const v = chip.knobs[k.id];
    return { label: k.label, value: k.kind === "int" ? `${v} ${k.unit}` : k.kind === "bool" ? (v === true ? k.on : k.off) : String(v) };
  });
}

/** The label of a version ("v2") for the API, or nothing when the run has no such version: the question is then about the run as a whole. */
export function versionLabelOf(run: RunState, candidateId: string | null | undefined): string | undefined {
  const label = candidateId ? run.candidates[candidateId]?.label : undefined;
  return label && /^v\d+$/.test(label) ? label : undefined;
}

// ---------- health ----------

/** The agent is up but its test machine is not (GET /health reason "sandbox_down"). */
const SANDBOX_DOWN = "Live runs are offline: the test machine is not ready.";

/** One plain line for the sidebar. */
export function healthLine(h: LiveHealth): string {
  switch (h.status) {
    case "checking":
      return "Checking live runs.";
    case "ready":
      return "Live runs are ready.";
    case "busy":
      return "Another live run is in progress.";
    case "budget_exhausted":
      return "Today's live budget is used up.";
    case "sandbox_down":
      return SANDBOX_DOWN;
    case "not_configured":
      return "Live runs are off on this site.";
    case "error":
      return "Live runs are offline right now.";
  }
}

/** Why Run cannot start while the agent is not ready, in plain words. */
export function notReady(h: LiveHealth): string {
  switch (h.status) {
    case "not_configured":
      return "Live runs are off on this site. The recorded run shows the same loop and costs nothing.";
    case "budget_exhausted":
      return "Today's live budget is used up. Live runs open again tomorrow.";
    case "busy":
      return "Another live run is in progress. Try again in a few minutes.";
    case "sandbox_down":
      return SANDBOX_DOWN;
    default:
      return "Live runs could not be reached. Verity's server did not answer.";
  }
}

// ---------- the run, in plain words ----------

export const GOAL_WORD: Record<Priority, string> = { area: "smaller", speed: "faster", power: "lower power" };

/** The square beside a step: done, failed, running, waiting, or skipped (grey, settled). */
export type StepKind = "pass" | "fail" | "run" | "wait" | "drop";
export type Step = { id: string; kind: StepKind; label: string };

/** "round 2 of 3" from the evolve stage ("R2 of 3"), else the bare round number from the run. */
function roundText(run: RunState): string | null {
  const m = /R(\d+) of (\d+)/.exec(run.stages.evolve.detail ?? "");
  if (m) return `round ${m[1]} of ${m[2]}`;
  const r = run.stages.evolve.round ?? run.round;
  return r > 0 ? `round ${r}` : null;
}

function stageStep(
  id: "plan" | "lock",
  run: RunState,
  words: { todo: string; now: string; done: string; failed: string; stopped: string },
): Step {
  const s = run.stages[id].status;
  if (s === "done" || s === "skipped") return { id, kind: "pass", label: words.done };
  if (s === "failed") return { id, kind: "fail", label: words.failed };
  // A step still open when the run ended: a stop by the user is not a failure, an error or the cap is.
  if (s === "active" && run.phase === "stopped") return { id, kind: "drop", label: words.stopped };
  if (s === "active") return { id, kind: TERMINAL.has(run.phase) ? "fail" : "run", label: TERMINAL.has(run.phase) ? words.failed : words.now };
  return { id, kind: "wait", label: words.todo };
}

/**
 * The five steps of a run in plain words, read from the run state only. Tool names stay out of these labels; the
 * "What each step means" dropdown carries them.
 */
export function runSteps(run: RunState, priority: Priority): Step[] {
  const ended = TERMINAL.has(run.phase);
  const goal = GOAL_WORD[priority];
  const steps: Step[] = [
    stageStep("plan", run, {
      todo: "Write a plan",
      now: "Writing a plan",
      done: "Plan written",
      failed: "The plan could not be written",
      stopped: "Stopped while writing the plan",
    }),
    stageStep("lock", run, {
      todo: "Lock the answer key",
      now: "Locking the answer key",
      done: "Answer key locked",
      failed: "The answer key could not be locked",
      stopped: "Stopped while locking the answer key",
    }),
  ];

  // The first version: done once v1 passes and is measured (the baseline every number is compared to).
  const v1 = run.candidateOrder.map((id) => run.candidates[id]).find((c): c is Candidate => c !== undefined && c.round === 0);
  const started = run.stages.rtl.status !== "pending" || v1 !== undefined;
  if (run.baselineId) steps.push({ id: "first", kind: "pass", label: "First version passes the tests" });
  else if (started && ended) {
    // Only a run that finished knows no version passed; a stop, an error or the cap says nothing about the design.
    steps.push({ id: "first", kind: "fail", label: run.phase === "done" ? "No first version passed the tests" : "Stopped before a first version passed the tests" });
  }
  else if (started) {
    const fixing = v1 && (v1.status === "fail" || v1.status === "fixing");
    steps.push({ id: "first", kind: "run", label: fixing ? "The first version failed a test. Fixing it" : "Writing the first version and testing it" });
  } else steps.push({ id: "first", kind: "wait", label: "Write a first version that passes the tests" });

  const ev = run.stages.evolve;
  const tried = Math.max(0, run.candidateOrder.length - 1);
  if (ev.status === "done") {
    steps.push({ id: "evolve", kind: "pass", label: tried === 1 ? `Tried 1 ${goal} version` : `Tried ${tried} ${goal} versions` });
  } else if (ev.status === "skipped") {
    steps.push({ id: "evolve", kind: "drop", label: `Skipped trying ${goal} versions${ev.detail ? ` (${ev.detail})` : ""}` });
  } else if (ev.status === "active" || ev.status === "failed") {
    const r = roundText(run);
    steps.push({
      id: "evolve",
      kind: ended ? "fail" : "run",
      label: ended ? `${run.phase === "stopped" ? "Stopped" : "Ended"} while trying ${goal} versions` : `Trying ${goal} versions${r ? ` (${r})` : ""}`,
    });
  } else steps.push({ id: "evolve", kind: "wait", label: `Try ${goal} versions` });

  const pr = run.stages.prove;
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  if (pr.status === "done") {
    const status = winner?.eqy?.status;
    steps.push(
      status === "proven"
        ? { id: "prove", kind: "pass", label: "Winner proven to work the same as v1" }
        : { id: "prove", kind: "fail", label: status === "not_equivalent" ? "The proof found a difference from v1" : "The winner could not be proven equal to v1" },
    );
  } else if (pr.status === "skipped") {
    steps.push({ id: "prove", kind: "drop", label: pr.detail === "v1 is the winner" ? "No proof needed: v1 is the winner" : `Proof skipped${pr.detail ? ` (${pr.detail})` : ""}` });
  } else if (pr.status === "active" || pr.status === "failed") {
    steps.push({ id: "prove", kind: ended ? "fail" : "run", label: ended ? "The proof did not finish" : "Proving the winner works the same as v1" });
  } else steps.push({ id: "prove", kind: "wait", label: "Prove the winner works the same as v1" });

  return steps;
}

/** How far along the run is, 0 to 1, for the thin bar. Settled steps count fully, the current one by half. */
export function progressOf(steps: Step[]): number {
  const settled = steps.filter((s) => s.kind === "pass" || s.kind === "fail" || s.kind === "drop").length;
  const running = steps.some((s) => s.kind === "run") ? 0.5 : 0;
  return Math.min(1, (settled + running) / steps.length);
}

/** Credits in words: "$0.21 spent". The server's spend limit is never shown as a figure. */
export function creditsLine(run: RunState): string {
  return `${formatUsd(run.credits.usedUsd)} spent`;
}

type Metric = "area" | "clock" | "power";

function valueOf(ppa: PPA | null, m: Metric): number | null {
  if (!ppa) return null;
  return m === "area" ? ppa.areaUm2 : m === "clock" ? ppa.clockPeriodNs : ppa.powerMw;
}

const METRIC_OF: Record<Priority, Metric> = { area: "area", speed: "clock", power: "power" };

/** A change against v1 in words, e.g. "1.6% smaller than v1". Null when either side was not measured. */
export function changeVsV1(m: Metric, value: number | null, base: number | null | undefined, baseLabel = "v1"): string | null {
  if (value === null || !base) return null;
  const d = formatDelta(value, base, { lowerIsBetter: true });
  const pct = `${Math.abs(((value - base) / base) * 100).toFixed(1)}%`;
  const better = d.direction === "better";
  if (d.direction === "neutral") {
    return m === "area" ? `about the same size as ${baseLabel}` : m === "clock" ? `about the same speed as ${baseLabel}` : `about the same power as ${baseLabel}`;
  }
  if (m === "area") return `${pct} ${better ? "smaller" : "larger"} than ${baseLabel}`;
  if (m === "clock") return `a ${pct} ${better ? "shorter" : "longer"} clock period than ${baseLabel}, so it runs ${better ? "faster" : "slower"}`;
  return `an estimated ${pct} ${better ? "less" : "more"} power than ${baseLabel}`;
}

/** The plain result sentence for a finished run: the winner and its change against v1 for the goal, from run data only. */
export function doneSentence(run: RunState, priority: Priority): string {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const baseLabel = base?.label ?? "v1";
  if (!winner) return "The run finished, but no version was measured, so there is no winner.";
  if (winner.id === base?.id) return `${winner.label} stayed the best. No later version beat it on the combined score.`;
  const m = METRIC_OF[priority];
  const change = changeVsV1(m, valueOf(winner.ppa, m), valueOf(base?.ppa ?? null, m), baseLabel);
  const lead =
    change === null
      ? `${winner.label} won on the combined score.`
      : m === "area"
        ? `${winner.label} won: it is ${change}.`
        : m === "clock"
          ? `${winner.label} won: it has ${change}.`
          : `${winner.label} won: it uses ${change}.`;
  const eqy = winner.eqy?.status;
  const proof =
    eqy === "proven"
      ? ` It is proven to work the same as ${baseLabel}.`
      : eqy === "not_equivalent"
        ? ` The proof found a difference from ${baseLabel}.`
        : ` It passes every test, but it was not proven to work the same as ${baseLabel}.`;
  return lead + proof;
}

/** Versions that beat the winner on the goal but lost on the combined score, each with the numbers that decided it. */
export function lostOnScore(run: RunState, priority: Priority): string[] {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  const base = run.baselineId ? run.candidates[run.baselineId] : undefined;
  if (!winner?.ppa || !winner.fitness || !base?.ppa) return [];
  const m = METRIC_OF[priority];
  const wv = valueOf(winner.ppa, m);
  if (wv === null) return [];
  const others: Metric[] = (["area", "clock", "power"] as const).filter((x) => x !== m);
  const name: Record<Metric, string> = { area: "area", clock: "clock period", power: "power" };
  const out: string[] = [];
  for (const id of run.candidateOrder) {
    const c = run.candidates[id];
    if (!c || c.id === winner.id || !c.ppa || !c.fitness) continue;
    const cv = valueOf(c.ppa, m);
    if (cv === null || cv >= wv) continue;
    const worse = others.find((x) => {
      const a = valueOf(c.ppa, x);
      const b = valueOf(winner.ppa, x);
      return a !== null && b !== null && a > b;
    });
    const bv = valueOf(base.ppa, m);
    const goalDelta = bv ? formatDelta(cv, bv, { lowerIsBetter: true }).text : null;
    const worseC = worse ? valueOf(c.ppa, worse) : null;
    const worseW = worse ? valueOf(winner.ppa, worse) : null;
    const worseB = worse ? valueOf(base.ppa, worse) : null;
    const worseText =
      worse && worseC !== null && worseW !== null && worseB
        ? `, but its ${name[worse]} was worse (${formatDelta(worseC, worseB, { lowerIsBetter: true }).text} vs v1, against ${formatDelta(worseW, worseB, { lowerIsBetter: true }).text} for ${winner.label})`
        : "";
    out.push(
      `${c.label} was ${GOAL_WORD[priority]} than ${winner.label}${goalDelta ? ` (${name[m]} ${goalDelta} vs v1)` : ""}${worseText}, so its combined score was lower.`,
    );
    if (out.length === 2) break;
  }
  return out;
}

/** The goal of a run: the agent's spec, else what this browser sent, else the default. */
export function goalOf(run: RunState | null, entry: RecentRun | undefined): Priority {
  return run?.spec?.priority ?? entry?.priority ?? "area";
}
