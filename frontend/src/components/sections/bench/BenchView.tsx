"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { PanelRightOpen } from "lucide-react";
import type { OpenTab } from "@/lib/contracts/agent";
import type { CpuJobView } from "@/lib/contracts/cpu";
import { isAccountsConfigured } from "@/lib/env";
import { useAccount } from "@/lib/hooks/use-account";
import { refreshAccountLists } from "@/lib/hooks/use-account-lists";
import { recheckHealth, useLiveHealth } from "@/lib/hooks/use-live-health";
import { useLiveRun } from "@/lib/hooks/use-live-run";
import { useLiveSession } from "@/lib/hooks/use-live-session";
import { useMediaQuery } from "@/lib/hooks/use-media-query";
import { markEnded, recordRun, saveAsks, storedAsks, useRecentRuns, type RunEnd } from "@/lib/hooks/use-recent-runs";
import { readOpenThread, readThread, saveThread, setOpenThread } from "@/lib/hooks/use-threads";
import { initialSnapshot } from "@/lib/run/reducer";
import { useLive, useRun, useUi, useVerityStore } from "@/lib/store";
import { useFollowFocus } from "../studio/detail/follow";
import { Sq } from "../studio/loop/LoopModule";
import { AppShell } from "./AppShell";
import { ArchitectureView, type Walk } from "./ArchitectureTab";
import { AskTurns, type ProposalTurn } from "./AskTurns";
import { ChatThread, EndMessage, RunningLine } from "./ChatThread";
import {
  agentMessages,
  askTurnFor,
  assumedWords,
  closestCard,
  goalOf,
  messageId,
  newThreadId,
  noParse,
  notReady,
  persistable,
  persistableMessages,
  pickCard,
  plainStartError,
  replyTo,
  seedThread,
  threadFromServer,
  threadOf,
  versionLabelOf,
  withoutAsked,
  type AskTurn,
  type ChatMessage,
  type Chip,
} from "./chat";
import { Composer } from "./Composer";
import { CpuPanel } from "./CpuPanel";
import { TERMINAL, humanRefOf } from "./derive";
import { ExamplePrompts } from "./ExamplePrompts";
import { OutputPanel, type PanelTab } from "./OutputPanel";
import type { RunRequest } from "./RequestModule";
import { Sidebar } from "./Sidebar";
import { signoffClean, signoffLine, signoffView } from "./Signoff";
import { VersionTab } from "./VersionTab";
import "./bench.css";

/** What the modules show before any run: an empty record, never the store's leftover replay. */
const IDLE_RUN = initialSnapshot({ runId: "bench", mode: "live", sample: false, title: "No run yet" }).run;

/**
 * A chat read back from the server does not carry the id of the CPU job it started, so its last CPU choice card would offer
 * "Choose this CPU" again and could start a second paid job. This gives that card the job of the thread (the root build
 * before an upgrade, else the newest), so it shows the job and never the choice. `known` is a job already read for this thread.
 */
function withCpuJob(messages: ChatMessage[], threadId: string, jobs: CpuJobView[], known?: CpuJobView): ChatMessage[] {
  const at = messages.findLastIndex((m) => m.kind === "cpu_choice");
  if (at < 0) return messages;
  const mine = jobs.filter((j) => j.threadId === threadId);
  const job = mine.find((j) => !j.parentJobId) ?? mine[0] ?? (known?.threadId === threadId ? known : undefined);
  return job ? messages.map((m, i) => (i === at && m.kind === "cpu_choice" && !m.jobId ? { ...m, jobId: job.id } : m)) : messages;
}

/** Owns the event stream of the run in the URL. Keyed by the page so Reconnect opens a fresh stream. */
function LiveFeed({ runId }: { runId: string }) {
  useLiveRun(runId);
  return null;
}

/** False on the server and during hydration, true after: what this browser kept is not read before the first client render agrees with the server's. */
function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

/**
 * /bench as a chat workspace (plan bench-chat-shell): a sidebar, a chat in the middle, and a right panel. Send reads
 * the sentence with POST /parse and answers with a chip card; Run starts the live run. The panel opens on Progress when
 * the run starts (900px and up) and moves to Result when it is done; the chat keeps one short line. The run id lives in
 * `?run=`, so a reload rejoins the stream, and the "Your runs" list rebuilds the chat.
 *
 * With the agent's "agent" feature (the chip engineer agent) the chat is one thread: every message, before or after a run,
 * goes to POST /threads/{id}/messages with the run on screen. The first message of a thread is fact-checked as before;
 * the rest are answered by the agent, with the tools it used. The thread is kept in this browser (`verity.threads`), so a
 * reload restores it, also before any run. Without the feature the old paths (POST /parse, then ask) run unchanged.
 */
export function BenchView() {
  const router = useRouter();
  const pathname = usePathname();
  const urlRun = useSearchParams().get("run");
  const cpuJobId = useSearchParams().get("cpuJob");
  const health = useLiveHealth();
  const live = useLive();
  const hasSession = useLiveSession();
  const account = useAccount();
  const signedIn = account.status === "signed_in";
  const threadParam = useSearchParams().get("thread");
  const recent = useRecentRuns();
  const storeRun = useRun((r) => r);
  const source = useVerityStore((s) => s.source);
  const storeRunId = useVerityStore((s) => s.runId);
  const log = useVerityStore((s) => s.log);
  const tailOpen = useVerityStore((s) => s.tailOpen);
  const { select, selection } = useUi();

  const [feed, setFeed] = useState(0);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [rejected, setRejected] = useState(false);
  const [stopping, setStopping] = useState(false);
  // State updates land after the click; the ref blocks a second Run fired in the same tick.
  const inFlight = useRef(false);
  // The questions under the run on screen. `askView` is what renders; `asksNow` is the same list for handlers that wake up
  // after an await. A run opened afresh reads its saved turns instead (see `turns`).
  const [askView, setAskView] = useState<{ runId: string; turns: AskTurn[] } | null>(null);
  const asksNow = useRef<{ runId: string; turns: AskTurn[] } | null>(null);
  const askAbort = useRef<AbortController | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // The chat thread (agent): its id is made here, one per thread, and sent with every message and with Run. The thread kept
  // in this browser was read once after hydration (`restored`). A panel tab the agent asked for waits for its run (`pendingTab`).
  const [threadId, setThreadId] = useState<string | null>(null);
  const [restored, setRestored] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [pendingTab, setPendingTab] = useState<OpenTab | null>(null);
  const [pickRequest, setPickRequest] = useState<{ id: string } | null>(null);
  const msgAbort = useRef<AbortController | null>(null);
  const hydrated = useHydrated();
  const [text, setText] = useState("");
  const [panelOpen, setPanelOpen] = useState(false);
  const [focusPanel, setFocusPanel] = useState(false);
  // The CPU board in the right panel: shown for the job in ?cpuJob=. It opens by itself when the job changes (where it fits
  // beside the chat), the X hides it, and Show board or the top-bar button bring it back.
  const [cpuPanelOpen, setCpuPanelOpen] = useState(false);
  const [cpuFocus, setCpuFocus] = useState(false);
  const [cpuSeen, setCpuSeen] = useState<string | null>(null);
  const [tab, setTab] = useState<PanelTab>("result");
  const [drawerOpen, setDrawerOpen] = useState(false);
  // The version on the chat box ("v4"): set by picking a version, kept apart from what the panel shows. Closing the panel
  // keeps it; the X, a new chip and another run clear it.
  const [attached, setAttached] = useState<{ runId: string; candidateId: string } | null>(null);
  // The Architecture tab moved into the main area (Expand), and the walkthrough an answer is showing.
  const [expanded, setExpanded] = useState(false);
  const [walk, setWalk] = useState<{ runId: string; turnId: string; index: number } | null>(null);
  // The panel sits beside the chat from 900px (the same breakpoint as the sheet in bench.css).
  const wide = useMediaQuery("(min-width: 900px)");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resultRef = useRef<HTMLButtonElement>(null);
  const showRef = useRef<HTMLButtonElement>(null);
  const openPanelRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const drawerFirstRef = useRef<HTMLButtonElement>(null);
  // The thread id as the async restore of a CPU job's chat sees it: a chat the reader began meanwhile is not overwritten.
  const threadIdRef = useRef<string | null>(null);
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  // A change of ?run= that this page made (Run, or a new chip after a finished run) keeps the thread. Any other change
  // (the sidebar, Back) starts from the stored run instead. Adjusted during render, so there is no frame of the old thread.
  const [seenUrl, setSeenUrl] = useState(urlRun);
  const [expectUrl, setExpectUrl] = useState<string | null | undefined>(undefined);
  // The run whose panel opened by itself at the end, and the run that was offered a panel at its start. Both clear with
  // every URL change, so a finished run opened from the list opens once, on Result.
  const [autoOpened, setAutoOpened] = useState<string | null>(null);
  const [startOpened, setStartOpened] = useState<string | null>(null);
  if (seenUrl !== urlRun) {
    setSeenUrl(urlRun);
    if (expectUrl !== urlRun) {
      // A run opened from the list restores the thread it started from, when this browser kept it; any other starts empty.
      const threadKept = urlRun ? recent.find((r) => r.id === urlRun)?.threadId : undefined;
      const kept = threadKept ? readThread(threadKept) : undefined;
      setThreadId(kept?.id ?? null);
      setMessages(kept ? kept.messages : []);
    }
    setExpectUrl(undefined);
    setAutoOpened(null);
    setStartOpened(null);
    setPanelOpen(false);
    setTab("result");
    setStartError(null);
    setAskView(null); // a run opened again shows what was saved, like a reload: a waiting or failed turn is not kept
    setAttached(null);
    setExpanded(false);
    setWalk(null);
  }

  const hasRun = urlRun !== null && source === "live" && storeRunId === urlRun && storeRun.runId === urlRun;
  const run = hasRun ? storeRun : IDLE_RUN;
  const follow = useFollowFocus(run);
  const active = hasRun && !TERMINAL.has(run.phase);
  const ended = hasRun && TERMINAL.has(run.phase);
  // An account is as good as a code, until the server turns its token away.
  const needsCode = (health.authRequired === true || rejected) && !(hasSession || (signedIn && !rejected));
  const human = useMemo(() => (hasRun ? humanRefOf(log) : null), [hasRun, log]);
  const entry = urlRun ? recent.find((r) => r.id === urlRun) : undefined;
  const stored = useMemo(() => threadOf(entry), [entry]);
  const thread = messages.length > 0 ? messages : stored;

  // The thread this browser kept: the open one on a fresh load, or the one of the run in the URL. Read once, after hydration,
  // so the first client render matches the server's.
  if (hydrated && !restored) {
    setRestored(true);
    // A chat being opened from the Chats list (?thread=) is read from the server, so the one this browser had open is not shown first.
    const threadKept = urlRun ? entry?.threadId : threadParam || cpuJobId ? undefined : (readOpenThread() ?? undefined);
    const kept = threadKept ? readThread(threadKept) : undefined;
    if (kept) {
      setThreadId(kept.id);
      setMessages(kept.messages);
    }
  }
  // The chip engineer agent answers every message when the server reports "agent". The thread decides the path, not the last
  // health answer: a thread with an id was started with the agent (only the agent path makes one), so every later message in it
  // goes to the agent too, even while /health does not answer. It is never sent down the old paths, which would clear it.
  const agentOn = health.features?.includes("agent") === true;
  const viaAgent = agentOn || threadId !== null;
  const threaded = viaAgent || messages.some((m) => m.kind === "run" || m.kind === "answer" || m.kind === "proposal" || m.kind === "cpu_proposal" || m.kind === "declined");
  // The run in view has a marker in the thread: the run lines show there, and the replies after it sit in the thread, not under the run.
  const markerHere = urlRun !== null && messages.some((m) => m.kind === "run" && m.runId === urlRun);
  // Whether a run belongs to this thread yet: a proposal then waits for Confirm, and only the first message is fact-checked.
  const threadHasRun = urlRun !== null || messages.some((m) => m.kind === "run");
  // "Try again" on a failed fact check: always on the old paths; in the agent's thread only while the check is the whole
  // conversation, because a retry asks on a new thread (only the first message of a thread is fact-checked).
  const canRetry =
    !threaded ||
    (urlRun === null && messages.filter((m) => m.kind === "user").length === 1 && messages.every((m) => m.kind === "user" || m.kind === "card" || m.kind === "refused" || m.kind === "error"));
  const priority = goalOf(hasRun ? run : null, entry);
  // What the user picked (the card and the list use it), so one chip has one name; the agent's own title only when this browser has none.
  const title = entry?.title ?? (hasRun ? run.title : null);
  const parsing = messages.some((m) => m.kind === "parsing");
  // The first /health answer has not come yet: which path a message takes is not known, so Send waits (as it does while parsing).
  const connecting = health.status === "checking";
  const turns: AskTurn[] = askView?.runId === urlRun ? askView.turns : (entry?.asks ?? []);
  const asking = turns.some((t) => t.kind === "asking");
  // A run that has ended can be asked about, when the agent says it can answer. Without that, the box still starts a new chip.
  const askOn = ended && urlRun !== null && (viaAgent || health.features?.includes("ask") === true);
  // This agent also looks up sources for a first message, so the wait is longer and says so.
  const researchOn = health.features?.includes("research") === true;
  // The version on the chat box, only while it belongs to the run in view.
  const attachedId = attached && attached.runId === urlRun && hasRun && run.candidates[attached.candidateId] ? attached.candidateId : null;
  const focusVersion = askOn ? versionLabelOf(run, attachedId) : undefined;
  // The version the panel shows: the one picked, else (for the Architecture tab) the winner, then v1, then the latest.
  const picked = hasRun && selection?.kind === "candidate" ? (run.candidates[selection.id] ?? null) : null;
  const settledId = run.winnerId ?? run.baselineId ?? run.candidateOrder.at(-1) ?? null;
  const archVersion = picked ?? (settledId ? (run.candidates[settledId] ?? null) : null);
  const signoff = useMemo(
    () => signoffView(run, { feature: health.features?.includes("signoff") === true, tailOpen }),
    [run, health.features, tailOpen],
  );
  const versions = useMemo(() => {
    const out: Record<string, string> = {};
    for (const cid of run.candidateOrder) {
      const c = run.candidates[cid];
      if (c) out[c.label] = c.id;
    }
    return out;
  }, [run.candidateOrder, run.candidates]);
  const big = expanded && hasRun;
  // One right panel at a time: a run's panel keeps priority over the CPU board.
  const runPanelShown = panelOpen && hasRun;
  const cpuPanelShown = !runPanelShown && cpuPanelOpen && cpuJobId !== null;
  // The chat box waits while a run is going; a lost connection frees it (the run may have ended unseen).
  const locked = starting || (urlRun !== null && !ended && live.connection !== "error");

  // A run that starts opens the panel on Progress, once per run id and only where it fits beside the chat. A phone gets the
  // Show progress button instead. Closing it keeps it closed until the run ends. A run this browser saw end is not "starting".
  if (hasRun && !ended && startOpened !== urlRun && entry?.ended === undefined) {
    setStartOpened(urlRun);
    if (wide) {
      setPanelOpen(true);
      setTab("progress");
      setFocusPanel(false);
    }
  }

  // When the run is done the panel shows the result: open on Result once per run (also when a finished run is opened from
  // the list), or switch to it if it is already open. Stopped and failed runs leave the panel where it is.
  if (hasRun && run.phase === "done" && autoOpened !== urlRun) {
    setAutoOpened(urlRun);
    setPanelOpen(true);
    setTab("result");
    setFocusPanel(false);
  }

  // The CPU board opens when the job in the URL changes (a job starts, or a link to one is opened), once the page is hydrated
  // and only where it fits beside the chat. A phone gets the Show board button instead. No job in the URL, no panel. A panel
  // already open (Show board for another job) stays as the reader opened it, focus included.
  if (hydrated && cpuJobId !== cpuSeen) {
    setCpuSeen(cpuJobId);
    if (!cpuJobId) setCpuPanelOpen(false);
    else if (wide && !cpuPanelOpen) {
      setCpuPanelOpen(true);
      setCpuFocus(false);
    }
  }

  // A panel tab the agent asked for opens once its run is on screen and read: the version it names is picked, and the
  // sign-off sheet is the winner's version tab.
  if (pendingTab && ended && urlRun !== null && pendingTab.runId === urlRun) {
    const named = pendingTab.version ? versions[pendingTab.version] : undefined;
    const target = pendingTab.tab === "signoff" ? (named ?? run.winnerId ?? run.baselineId ?? undefined) : named;
    setPendingTab(null);
    if (target) {
      setPickRequest({ id: target });
      setAttached({ runId: urlRun, candidateId: target });
    }
    setTab(pendingTab.tab === "signoff" ? (target ? "version" : "result") : pendingTab.tab);
    setPanelOpen(true);
    setFocusPanel(false);
  }

  // The "Your runs" list notes how each run ended, once this browser has seen it end.
  const endedAs: RunEnd | null = !ended ? null : run.phase === "done" ? "done" : run.phase === "stopped" ? "stopped" : "failed";
  useEffect(() => {
    if (!urlRun || !endedAs) return;
    markEnded(urlRun, endedAs);
    refreshAccountLists(); // the account's run list now says how it ended
  }, [urlRun, endedAs]);

  // A run that fails may have lost its test machine or model service: ask /health again, so the sidebar and the next Run
  // do not still say ready. The sentence on the failed run is what the server wrote; this only refreshes the status.
  const failed = ended && run.phase === "error";
  useEffect(() => {
    if (failed) recheckHealth();
  }, [failed, urlRun]);

  // Whether a finished run can be asked about comes from /health. If that check failed on load, ask again once, so the first
  // question is not read as a new chip. A health that stays "error" does not change `health.status`, so this runs once.
  const healthDown = health.status === "error";
  useEffect(() => {
    if (ended && healthDown) recheckHealth();
  }, [ended, healthDown, urlRun]);

  // A server that asks for a sign-in sends a reader with neither an account nor an access code to the sign-in page, which
  // brings them back here. Nothing is decided until the saved account has been read, so a signed-in reader never bounces.
  const gated = isAccountsConfigured && health.authRequired === true && !hasSession && account.status === "signed_out";
  useEffect(() => {
    if (gated && hydrated) router.replace(`/signin?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }, [gated, hydrated, router]);

  // A chat opened from the sidebar's Chats list (?thread=): read from the server, rebuilt as messages, and continued with the
  // same thread id. The chat's latest run comes into view with it, and ?thread= gives way to ?run=. A chat that cannot be read says so.
  useEffect(() => {
    if (!threadParam) return;
    const ac = new AbortController();
    void (async () => {
      const { listMyCpuJobs, listMyRuns, readThread } = await import("@/lib/sources/live");
      const [view, mine, jobs] = await Promise.all([readThread(threadParam, ac.signal), listMyRuns(ac.signal), listMyCpuJobs(ac.signal)]);
      if (ac.signal.aborted) return;
      if (!view.ok) {
        setOpenError(view.reason === "not_found" ? "That chat could not be found." : "That chat could not be opened right now.");
        router.replace(pathname);
        return;
      }
      // Every run of the chat: the ones the chat names, and the ones the account's run list says started from it.
      const times = new Map((mine.ok ? mine.value : []).map((r) => [r.id, r.createdAt]));
      const ids = new Set(view.value.runIds);
      for (const r of mine.ok ? mine.value : []) if (r.threadId === view.value.threadId) ids.add(r.id);
      const built = withCpuJob(threadFromServer(view.value.turns, [...ids].map((id) => ({ id, createdAt: times.get(id) ?? "" }))), view.value.threadId, jobs);
      const last = built.findLast((m) => m.kind === "run");
      const lastRun = last?.kind === "run" ? last.runId : null;
      msgAbort.current?.abort();
      msgAbort.current = null;
      setOpenError(null);
      setRestored(true);
      setThreadId(view.value.threadId);
      setMessages(built);
      setPendingTab(null);
      setText("");
      setStartError(null);
      setAttached(null);
      setExpanded(false);
      setWalk(null);
      select(null);
      if (lastRun) setExpectUrl(lastRun); // the run comes with its chat, which is already on screen
      router.replace(lastRun ? `${pathname}?run=${encodeURIComponent(lastRun)}` : pathname);
    })();
    return () => ac.abort();
  }, [threadParam, pathname, router, select]);

  // A page opened on ?cpuJob= brings back the chat the job was started from: the job names its thread, which is read from the
  // server like a chat from the Chats list. ?cpuJob= stays in the URL and the board stays in the panel. A job with no chat, or a
  // chat that cannot be read, leaves the chat empty. A chat already on screen for that job, or one the reader began meanwhile,
  // is left alone.
  useEffect(() => {
    if (!cpuJobId || !hydrated || threadParam) return;
    const before = threadIdRef.current;
    const ac = new AbortController();
    void (async () => {
      const { getCpuJob, listMyCpuJobs, listMyRuns, readThread } = await import("@/lib/sources/live");
      const got = await getCpuJob(cpuJobId, ac.signal);
      if (ac.signal.aborted || !got.ok) return;
      const tid = got.job.threadId;
      if (!tid || tid === threadIdRef.current) return;
      const [view, mine, jobs] = await Promise.all([readThread(tid, ac.signal), listMyRuns(ac.signal), listMyCpuJobs(ac.signal)]);
      if (ac.signal.aborted || !view.ok || threadIdRef.current !== before) return;
      const times = new Map((mine.ok ? mine.value : []).map((r) => [r.id, r.createdAt]));
      const ids = new Set(view.value.runIds);
      for (const r of mine.ok ? mine.value : []) if (r.threadId === view.value.threadId) ids.add(r.id);
      const built = withCpuJob(threadFromServer(view.value.turns, [...ids].map((id) => ({ id, createdAt: times.get(id) ?? "" }))), view.value.threadId, jobs, got.job);
      msgAbort.current?.abort();
      msgAbort.current = null;
      setRestored(true);
      setThreadId(view.value.threadId);
      setMessages(built);
      setPendingTab(null);
      setStartError(null);
      setAttached(null);
      setWalk(null);
    })();
    return () => ac.abort();
  }, [cpuJobId, hydrated, threadParam]);

  // Closing hands focus back to a button that opens the panel (the one in the top bar; Show progress or the result card
  // while the architecture is expanded), but only when focus was inside the panel.
  const closePanel = useCallback(() => {
    const inside = panelRef.current?.contains(document.activeElement) === true;
    setPanelOpen(false);
    if (inside) requestAnimationFrame(() => (openPanelRef.current ?? showRef.current ?? resultRef.current ?? inputRef.current)?.focus());
  }, []);

  const closeCpuPanel = useCallback(() => {
    const inside = panelRef.current?.contains(document.activeElement) === true;
    setCpuPanelOpen(false);
    if (inside) requestAnimationFrame(() => (openPanelRef.current ?? inputRef.current)?.focus());
  }, []);

  const openCpuPanel = () => {
    setCpuFocus(true);
    setCpuPanelOpen(true);
  };

  /** "Show board" on a job card: the board opens in the right panel, and the URL follows when it is another job. The chat stays. */
  const showBoard = (jobId: string) => {
    if (jobId !== cpuJobId) {
      if (urlRun) setExpectUrl(null); // leaving a run for a job keeps the thread
      router.replace(`${pathname}?cpuJob=${encodeURIComponent(jobId)}`);
    }
    openCpuPanel();
  };

  // The top bar's Open the panel: the tab left open, unless it has nothing to show yet (Result while the run goes) or
  // nothing new (Progress once it ended).
  const openPanel = () => {
    setTab((t) => (ended && t === "progress" ? "result" : !ended && t === "result" ? "progress" : t));
    setPanelOpen(true);
    setFocusPanel(true);
  };

  // Escape closes the drawer first, then the panel, then the expanded architecture view.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (drawerOpen) setDrawerOpen(false);
      else if (runPanelShown) closePanel();
      else if (cpuPanelShown) closeCpuPanel();
      else if (expanded) setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen, runPanelShown, cpuPanelShown, expanded, closePanel, closeCpuPanel]);

  /**
   * A version picked anywhere (a Versions row, a picker, a name in an answer): the panel shows it and the chat box takes
   * it as a chip. `open` also brings up its own tab ("v4"); a picker keeps the tab it is on.
   */
  const pickVersion = (id: string, open: boolean) => {
    if (!urlRun || !run.candidates[id]) return;
    select({ kind: "candidate", id });
    setAttached({ runId: urlRun, candidateId: id });
    if (open) {
      setTab("version");
      setPanelOpen(true);
      setFocusPanel(false);
    }
  };

  /** "Ask about v4" from its tab: the chip goes on the chat box, and a phone's sheet steps aside for it. */
  const attachTo = (id: string) => {
    if (!urlRun) return;
    setAttached({ runId: urlRun, candidateId: id });
    if (!wide) setPanelOpen(false);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  /** Shows an answer's walkthrough from its first step on the Architecture tab (the panel, or the expanded view). */
  const startWalk = (turnId: string) => {
    if (!urlRun) return;
    setWalk({ runId: urlRun, turnId, index: 0 });
    if (!expanded) {
      setTab("architecture");
      setPanelOpen(true);
      setFocusPanel(false);
    }
  };

  const expand = () => {
    setExpanded(true);
    setPanelOpen(false);
  };
  const collapse = () => {
    setExpanded(false);
    // Beside the chat there is room for the panel again; a phone goes back to the chat itself.
    if (wide) {
      setTab("architecture");
      setPanelOpen(true);
    }
  };

  /** Verity reads one sentence and answers in place of the "Reading..." line. A cleared thread drops the answer. */
  const ask = async (sentence: string, pendingId: string) => {
    let reply: ChatMessage;
    if (health.status === "not_configured") reply = { id: messageId(), kind: "error", prompt: sentence, text: notReady(health) };
    else if (health.status !== "checking" && health.status !== "error" && !health.features?.includes("parse")) reply = noParse(sentence);
    else {
      const { postParse } = await import("@/lib/sources/live");
      const res = await postParse(sentence);
      reply = replyTo(sentence, res);
      // The sidebar and the card read the health from page load; a failed (or, after an error, a successful) answer means it may have changed.
      if (!res.ok || health.status === "error") recheckHealth();
    }
    setMessages((prev) => prev.map((m) => (m.id === pendingId ? reply : m)));
  };

  /** A sentence that starts a thread of its own. The run on screen stays under "Your runs". */
  const beginThread = (sentence: string) => {
    const user: ChatMessage = { id: messageId(), kind: "user", text: sentence };
    const pending: ChatMessage = { id: messageId(), kind: "parsing", checking: researchOn };
    setText("");
    setStartError(null);
    setMessages([user, pending]);
    setExpectUrl(null);
    if (urlRun) router.replace(pathname);
    void ask(sentence, pending.id);
  };

  /**
   * One message to the chip engineer agent. The first of a thread is fact-checked; the rest are answered by the agent. The
   * run on screen and the version on the chat box go with it. A run this browser has no thread for brings its saved
   * conversation into the thread first, so nothing already on screen is lost.
   */
  const sendAgent = (sentence: string, keepText: boolean) => {
    const first = !threadHasRun && !messages.some((m) => m.kind === "user");
    const tid = threadId ?? newThreadId();
    if (!threadId) setThreadId(tid);
    const focus = urlRun ? focusVersion : undefined;
    const user: ChatMessage = focus ? { id: messageId(), kind: "user", text: sentence, version: focus } : { id: messageId(), kind: "user", text: sentence };
    const pending: ChatMessage = { id: messageId(), kind: "parsing", checking: first && researchOn, thinking: !first };
    if (!keepText) setText("");
    setStartError(null);
    setMessages([...(urlRun && !markerHere ? seedThread(entry, urlRun, messages) : messages), user, pending]);
    void deliver(tid, sentence, pending.id, { runId: urlRun, hasRun: threadHasRun, first }, focus);
  };

  /** Sends the message and puts the reply in place of the waiting line. A failure says why in a sentence, and a later question comes back to the chat box. */
  const deliver = async (
    tid: string,
    sentence: string,
    pendingId: string,
    ctx: { runId: string | null; hasRun: boolean; first: boolean },
    focus: string | undefined,
  ) => {
    msgAbort.current?.abort();
    const ac = new AbortController();
    msgAbort.current = ac;
    const { postMessage } = await import("@/lib/sources/live");
    const res = await postMessage(tid, sentence, ctx.runId ?? undefined, focus, ac.signal, cpuJobId ?? undefined);
    if (msgAbort.current === ac) msgAbort.current = null;
    // The reader moved to another thread before the answer came: nothing to show for that message.
    if (!res.ok && res.reason === "aborted") return setMessages((prev) => prev.filter((m) => m.id !== pendingId));
    const replies = agentMessages(sentence, res, ctx);
    setMessages((prev) => {
      const at = prev.findIndex((m) => m.id === pendingId);
      return at < 0 ? prev : [...prev.slice(0, at), ...replies, ...prev.slice(at + 1)];
    });
    if (!res.ok) {
      if (res.reason === "unauthorized") {
        if (!ctx.first) setRejected(true);
      } else {
        if (!ctx.first) setText((t) => (t.trim() === "" ? sentence : t)); // the question comes back, so it can be sent again or reworded
        recheckHealth();
      }
      return;
    }
    if (health.status === "error") recheckHealth();
    refreshAccountLists(); // the chat is now on the account's list, or moved to its top
    // A walkthrough opens on the Architecture tab beside the chat; a phone keeps the chat and offers it in the answer.
    const answer = replies.find((m) => m.kind === "answer");
    if (answer?.kind === "answer" && answer.steps && ctx.runId) {
      setWalk({ runId: ctx.runId, turnId: answer.id, index: 0 });
      if (!expanded && wide) {
        setTab("architecture");
        setPanelOpen(true);
      }
    }
    const open = res.reply?.kind === "answer" ? res.reply.openTab : undefined;
    if (open) showTab(open);
  };

  /** The agent asked to show a panel tab. A run of this thread that is not on screen is opened first (the thread stays), then the tab follows. */
  const showTab = (open: OpenTab) => {
    if (open.runId !== urlRun) {
      if (!recent.some((r) => r.id === open.runId)) return; // a run this browser did not start cannot be opened from here
      setExpectUrl(open.runId);
      router.replace(`${pathname}?run=${encodeURIComponent(open.runId)}`);
    }
    setPendingTab(open);
  };

  /** The same message goes to the agent again in place of the reply it got ("Try again", or after an access code was accepted). */
  const resend = (messageIdOfReply: string, sentence: string) => {
    const first = !threadHasRun && messages.filter((m) => m.kind === "user").length <= 1;
    // A first message that failed or timed out is sent on a new thread: the server may have run (and paid for) its fact check
    // without this page seeing it, and only the first message of a thread is fact-checked. A later message stays on its thread.
    const tid = first ? newThreadId() : (threadId ?? newThreadId());
    if (tid !== threadId) setThreadId(tid);
    const pending: ChatMessage = { id: messageId(), kind: "parsing", checking: first && researchOn, thinking: !first };
    setText((t) => (t === sentence ? "" : t)); // a failed question was put back in the box; it is being sent again
    setMessages((prev) => prev.map((m) => (m.id === messageIdOfReply ? pending : m)));
    void deliver(tid, sentence, pending.id, { runId: urlRun, hasRun: threadHasRun, first }, urlRun ? focusVersion : undefined);
  };

  const send = () => sendSentence(text.trim(), false);

  /** `keepText`: a suggestion chip sends its own words and leaves what the reader was typing. */
  const sendSentence = (sentence: string, keepText: boolean) => {
    if (!sentence || parsing || asking || locked || connecting) return;
    if (viaAgent) return sendAgent(sentence, keepText);
    if (askOn) {
      setText("");
      void askRun(sentence);
      return;
    }
    // A finished run is on screen: a new chip starts a new thread.
    if (urlRun) return beginThread(sentence);
    const user: ChatMessage = { id: messageId(), kind: "user", text: sentence };
    const pending: ChatMessage = { id: messageId(), kind: "parsing", checking: researchOn };
    setText("");
    setStartError(null);
    setMessages((prev) => [...prev, user, pending]);
    void ask(sentence, pending.id);
  };

  /** Puts the list of turns under a run on screen, and keeps the part worth keeping in this browser. */
  const putTurns = (runId: string, next: AskTurn[]) => {
    asksNow.current = { runId, turns: next };
    setAskView({ runId, turns: next });
    saveAsks(runId, persistable(next));
  };
  const turnsOf = (runId: string): AskTurn[] => (asksNow.current?.runId === runId ? asksNow.current.turns : storedAsks(runId));

  /** One question about the ended run in view. The answer lands in place of the waiting line; a failure says why in a sentence. */
  const askRun = async (sentence: string) => {
    if (!urlRun) return;
    const runId = urlRun;
    const focus = focusVersion;
    // The question keeps the version it was about, so the thread (and a reload) shows it.
    const user: AskTurn = focus ? { id: messageId(), kind: "user", text: sentence, version: focus } : { id: messageId(), kind: "user", text: sentence };
    const waiting: AskTurn = { id: messageId(), kind: "asking" };
    putTurns(runId, [...turnsOf(runId), user, waiting]);
    askAbort.current?.abort();
    const ac = new AbortController();
    askAbort.current = ac;
    const { postAsk } = await import("@/lib/sources/live");
    const res = await postAsk(runId, sentence, focus, ac.signal);
    if (askAbort.current === ac) askAbort.current = null;
    const rest = turnsOf(runId).filter((t) => t.id !== waiting.id);
    // The reader moved on before the answer came: nothing to show for that question.
    if (!res.ok && res.reason === "aborted") return putTurns(runId, rest.filter((t) => t.id !== user.id));
    const reply = askTurnFor(sentence, res);
    putTurns(runId, [...rest, reply]);
    // A walkthrough opens on the Architecture tab beside the chat; a phone keeps the chat and offers it in the answer.
    if (reply.kind === "answer" && reply.steps) {
      setWalk({ runId, turnId: reply.id, index: 0 });
      if (!expanded && wide) {
        setTab("architecture");
        setPanelOpen(true);
      }
    }
    if (res.ok) return;
    if (res.reason === "unauthorized") setRejected(true);
    else {
      setText((t) => (t.trim() === "" ? sentence : t)); // the question comes back, so it can be sent again or reworded
      recheckHealth();
    }
  };

  /** An access code typed into a failed answer: once accepted, the same question is asked again. */
  const codeThenAskRun = async (turnId: string, sentence: string, code: string): Promise<string | null> => {
    const error = await submitCode(code);
    if (error) return error;
    if (urlRun) putTurns(urlRun, withoutAsked(turnsOf(urlRun), turnId));
    void askRun(sentence);
    return null;
  };

  const confirmProposal = async (turn: Extract<AskTurn, { kind: "proposal" }>, req: RunRequest) => {
    const from = urlRun;
    const started = await startRun(req, turn.prompt, null, true);
    if (from && started) putTurns(from, turnsOf(from).map((t) => (t.id === turn.id && t.kind === "proposal" ? { ...t, state: "started", newRunId: started } : t)));
  };

  const dismissProposal = (turnId: string) => {
    if (urlRun) putTurns(urlRun, turnsOf(urlRun).map((t) => (t.id === turnId && t.kind === "proposal" ? { ...t, state: "dismissed" } : t)));
  };

  // The same two for a proposal that is a message of the agent's thread: the run joins the thread, and the proposal keeps a link to it.
  const confirmThreadProposal = async (turn: ProposalTurn, req: RunRequest) => {
    const started = await startRun(req, turn.prompt, null);
    if (started) setMessages((prev) => prev.map((m) => (m.id === turn.id && m.kind === "proposal" ? { ...m, state: "started", newRunId: started } : m)));
  };
  const dismissThreadProposal = (turnId: string) => {
    setMessages((prev) => prev.map((m) => (m.id === turnId && m.kind === "proposal" ? { ...m, state: "dismissed" } : m)));
  };

  // Leaving a run drops a question still waiting on it, and the turns held in memory for it.
  useEffect(
    () => () => {
      askAbort.current?.abort();
      askAbort.current = null;
      msgAbort.current?.abort();
      msgAbort.current = null;
      asksNow.current = null;
    },
    [urlRun],
  );

  // The thread is kept in this browser after every change, and the open one is noted, so a reload restores it, also before any run.
  useEffect(() => {
    if (threadId) saveThread(threadId, persistableMessages(messages));
  }, [threadId, messages]);
  useEffect(() => {
    if (restored) setOpenThread(threadId);
  }, [restored, threadId]);

  // The version a tab opened by the agent picks reaches the panel's selection (the store) from here.
  useEffect(() => {
    if (pickRequest) select({ kind: "candidate", id: pickRequest.id });
  }, [pickRequest, select]);

  const add = (msg: ChatMessage) => {
    if (agentOn && !threadId) setThreadId(newThreadId());
    setMessages((prev) => [...prev, msg]);
  };

  const submitCode = async (code: string): Promise<string | null> => {
    const { createSession } = await import("@/lib/sources/live");
    const result = await createSession(code);
    if (!result.ok) return result.error;
    setRejected(false);
    setStartError(null);
    return null;
  };

  /** The same sentence is read again in place of the reply it got ("Try again", or after an access code was accepted). */
  const readAgain = (messageIdOfReply: string, sentence: string) => {
    if (viaAgent) return resend(messageIdOfReply, sentence);
    const pending: ChatMessage = { id: messageId(), kind: "parsing", checking: researchOn };
    setMessages((prev) => prev.map((m) => (m.id === messageIdOfReply ? pending : m)));
    void ask(sentence, pending.id);
  };

  /** An access code typed into a reply: once accepted, the same sentence is read again. */
  const codeThenAsk = async (messageIdOfReply: string, sentence: string, code: string): Promise<string | null> => {
    const error = await submitCode(code);
    if (error) return error;
    readAgain(messageIdOfReply, sentence);
    return null;
  };

  /** Starts a run and returns its id, or null when it did not start. `fresh`: the new run opens its own thread (a proposal from a question). */
  const startRun = async (req: RunRequest, prompt: string, cardId: string | null, fresh = false): Promise<string | null> => {
    if (inFlight.current || starting || active) return null;
    inFlight.current = true;
    setStarting(true);
    setStartError(null);
    select(null);
    // A run started from the agent's thread names the thread, so the agent writes the run into that thread's facts.
    const tid = viaAgent ? (threadId ?? newThreadId()) : undefined;
    if (tid && !threadId) setThreadId(tid);
    const res = await live.startLive(req.familyId, req.priority, req.sendKnobs ? req.knobs : undefined, req.title, req.researchId, tid);
    setStarting(false);
    inFlight.current = false;
    if ("runId" in res) {
      setStopping(false);
      // The first reply's fact check and assumptions go with the run, so a reload of ?run= shows them again. What was
      // assumed only holds for the kind of chip that was read.
      const card = cardId ? messages.find((m) => m.id === cardId) : undefined;
      const check = card?.kind === "card" ? card.check : undefined;
      const assumed = card?.kind === "card" && card.familyId === req.familyId ? card.assumed : undefined;
      recordRun({
        id: res.runId,
        title: req.title,
        prompt,
        familyId: req.familyId,
        knobs: req.knobs,
        priority: req.priority,
        sendKnobs: req.sendKnobs,
        startedAt: Date.now(),
        ...(check ? { check } : {}),
        ...(assumed ? { assumed } : {}),
        ...(tid ? { threadId: tid } : {}),
      });
      if (cardId) {
        setMessages((prev) =>
          prev.map((m) => (m.id === cardId && m.kind === "card" ? { ...m, familyId: req.familyId, knobs: req.knobs, priority: req.priority, sent: true } : m)),
        );
      }
      // The run gets its marker in the thread, where its progress and its end show. A run on screen without one is brought in first.
      if (tid) {
        const marker: ChatMessage = { id: messageId(), kind: "run", runId: res.runId };
        setMessages((prev) => [...(urlRun && !prev.some((m) => m.kind === "run" && m.runId === urlRun) ? seedThread(entry, urlRun, prev) : prev), marker]);
      }
      if (fresh) setMessages([]); // the thread is rebuilt from the run just listed: the question, then the chip that was sent
      setExpectUrl(res.runId);
      router.replace(`${pathname}?run=${encodeURIComponent(res.runId)}`);
      refreshAccountLists(); // the run is now on the account's list
      return res.runId;
    }
    if (res.reason === "unauthorized") setRejected(true);
    else {
      setStartError(plainStartError(res.error));
      recheckHealth();
    }
    return null;
  };

  const rerun = () => {
    if (!entry) return;
    void startRun(
      { familyId: entry.familyId, knobs: entry.knobs, priority: entry.priority, title: entry.title, sendKnobs: entry.sendKnobs },
      entry.prompt,
      null,
    );
  };

  const stop = async () => {
    setStopping(true);
    const before = useVerityStore.getState().error;
    await live.stopRun();
    const after = useVerityStore.getState().error;
    if (after && after !== before) setStopping(false);
  };

  const showProgress = () => {
    setTab("progress");
    setPanelOpen(true);
    setFocusPanel(true);
  };

  const newChip = () => {
    msgAbort.current?.abort();
    msgAbort.current = null;
    setMessages([]);
    setThreadId(null); // a new thread: the next message gets a new id, and the old thread stays in this browser
    setPendingTab(null);
    setText("");
    setPanelOpen(false);
    setCpuPanelOpen(false);
    setDrawerOpen(false);
    setStartError(null);
    setAttached(null);
    setExpanded(false);
    setWalk(null);
    select(null);
    if (urlRun || cpuJobId) router.push(pathname);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const fillExample = (example: string) => {
    setText(example);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(example.length, example.length);
    });
  };

  const empty = thread.length === 0 && !urlRun && !cpuJobId;
  const endKey = ended ? run.phase : "live";
  const lastId = thread.at(-1)?.id ?? "none";

  // The walkthrough of an answer under this run. Moving through it never calls the model.
  // With the agent's thread (a marker for this run) the answer is a message of the thread; else it is a turn saved under the run.
  const walkTurn = walk && walk.runId === urlRun ? (markerHere ? messages.find((m) => m.id === walk.turnId) : turns.find((t) => t.id === walk.turnId)) : undefined;
  const walkSteps = walkTurn?.kind === "answer" ? walkTurn.steps : undefined;
  const walkView: Walk | null =
    walk && walkSteps
      ? {
          steps: walkSteps,
          index: Math.min(walk.index, walkSteps.length - 1),
          onBack: () => setWalk((w) => (w ? { ...w, index: Math.max(0, w.index - 1) } : w)),
          onNext: () => setWalk((w) => (w ? { ...w, index: Math.min(walkSteps.length - 1, w.index + 1) } : w)),
          onEnd: () => setWalk(null),
        }
      : null;
  // What the first reply assumed for the knobs the sentence did not state, for the Spec step.
  const firstCard = thread.find((m) => m.kind === "card" && m.sent);
  const assumed = firstCard?.kind === "card" && firstCard.assumed ? assumedWords(firstCard.familyId, firstCard.knobs, firstCard.assumed) : null;
  const baseLabel = run.baselineId ? (run.candidates[run.baselineId]?.label ?? "v1") : "v1";
  const architecture = (isBig: boolean) => (
    <ArchitectureView
      run={run}
      version={archVersion}
      human={human}
      signoff={signoff}
      assumed={assumed}
      walk={walkView}
      big={isBig}
      onPickVersion={(id) => pickVersion(id, false)}
      onExpand={isBig ? collapse : expand}
    />
  );
  const winnerId = run.winnerId;
  // The sign-off sheet in one line on the Result tab, or what stands in for it. Nothing from a server without the checks.
  const signoffNote =
    signoff.state === "ok" ? (
      <div className="b-so-line">
        <p className="b-note">
          <Sq kind={signoffClean(signoff.sheet) ? "pass" : "fail"} /> {signoffLine(signoff.sheet, baseLabel)}
        </p>
        {winnerId ? (
          <button type="button" className="b-link" onClick={() => pickVersion(winnerId, true)}>
            Open the sign-off sheet
          </button>
        ) : null}
      </div>
    ) : signoff.state === "running" ? (
      <p className="b-note" role="status">
        <Sq kind="run" /> Sign-off checks running
      </p>
    ) : signoff.state === "bad" ? (
      <p className="b-note b-dim">The sign-off sheet came, but it could not be read.</p>
    ) : signoff.state === "none" && run.phase === "done" ? (
      <p className="b-note b-dim">No sign-off sheet came for this run.</p>
    ) : null;
  // The version tab exists only while a version is picked.
  const shownTab: PanelTab = tab === "version" && !picked ? "versions" : tab;
  // The expanded view keeps the newest reply in sight; the full answer (and any buttons) stays in the chat.
  const lastTurn = turns.at(-1);
  const tail = thread.at(-1);
  const bigWaiting = markerHere ? tail?.kind === "parsing" : lastTurn?.kind === "asking";
  const bigText = markerHere
    ? tail?.kind === "answer" || tail?.kind === "proposal" || tail?.kind === "cpu_proposal" || tail?.kind === "declined"
      ? tail.text
      : null
    : lastTurn && lastTurn.kind !== "user" && lastTurn.kind !== "asking"
      ? lastTurn.text
      : null;
  const bigReply = bigWaiting ? (
    <p className="b-big-reply b-wait" role="status">
      <Sq kind="run" /> Looking through this run...
    </p>
  ) : bigText !== null ? (
    <div className="b-big-reply" role="status">
      <p>
        <b>Latest reply:</b> {bigText}
      </p>
      <button type="button" className="b-link" onClick={collapse}>
        Read it in the chat
      </button>
    </div>
  ) : null;

  if (gated) return null; // on its way to the sign-in page

  return (
    <AppShell
      sidebar={
        <Sidebar
          runs={recent}
          currentRun={urlRun}
          currentThread={threadId}
          health={health}
          open={drawerOpen}
          firstRef={drawerFirstRef}
          onNewChip={newChip}
          onNavigate={() => setDrawerOpen(false)}
        />
      }
      panel={
        runPanelShown ? (
          <OutputPanel
            run={run}
            title={title ?? run.title}
            tab={shownTab}
            onTab={setTab}
            onClose={closePanel}
            follow={follow}
            human={human}
            humanFeature={health.features?.includes("human_ref") === true}
            priority={priority}
            connection={live.connection}
            liveError={live.error}
            stopping={stopping && active}
            onStop={() => void stop()}
            onReconnect={() => setFeed((n) => n + 1)}
            focusOnOpen={focusPanel}
            panelRef={panelRef}
            onPickVersion={pickVersion}
            versionLabel={picked?.label ?? null}
            versionTab={
              picked ? (
                <VersionTab
                  run={run}
                  c={picked}
                  human={human}
                  signoff={signoff}
                  attached={attachedId === picked.id}
                  canAsk={askOn}
                  onAttach={() => attachTo(picked.id)}
                  onPickVersion={(id) => pickVersion(id, true)}
                />
              ) : null
            }
            architecture={architecture(false)}
            signoffLine={signoffNote}
          />
        ) : cpuPanelShown && cpuJobId ? (
          <CpuPanel id={cpuJobId} onClose={closeCpuPanel} focusOnOpen={cpuFocus} panelRef={panelRef} />
        ) : null
      }
      drawerOpen={drawerOpen}
      onDrawer={setDrawerOpen}
      drawerFirstRef={drawerFirstRef}
      title={urlRun ? title : null}
      empty={empty}
      actions={
        (hasRun || cpuJobId !== null) && !runPanelShown && !cpuPanelShown && !big ? (
          <button ref={openPanelRef} type="button" className="b-ib b-top-end" aria-label="Open the panel" onClick={hasRun ? openPanel : openCpuPanel}>
            <PanelRightOpen aria-hidden />
          </button>
        ) : null
      }
    >
      {urlRun ? <LiveFeed key={`${urlRun}:${feed}`} runId={urlRun} /> : null}
      {big ? (
        <div className="b-big">
          <h1 className="sr">Verity bench: architecture</h1>
          {bigReply}
          {architecture(true)}
        </div>
      ) : empty && threadParam ? (
        <div className="b-hello">
          <p role="status">Opening your chat...</p>
        </div>
      ) : empty ? (
        <div className="b-hello">
          <h1>What chip should Verity build?</h1>
          <p>Describe it in one sentence. Verity writes it, tests it, and then tries smaller or faster versions.</p>
          {openError ? <p role="alert">{openError}</p> : null}
        </div>
      ) : (
        <>
        <h1 className="sr">Verity bench</h1>
        <ChatThread
          cpuCanBuild={account.canBuild}
          threadId={threadId}
          onCpuStarted={(messageId, jobId) => {
            setMessages((prev) => prev.map((m) => m.id === messageId && m.kind === "cpu_choice" ? { ...m, jobId } : m));
            if (urlRun) setExpectUrl(null); // the job joins this thread; leaving the run must not rebuild it
            router.replace(`${pathname}?cpuJob=${encodeURIComponent(jobId)}`);
          }}
          onCpuProposalStarted={(messageId, jobId) => {
            setMessages((prev) => prev.map((m) => (m.id === messageId && m.kind === "cpu_proposal" ? { ...m, state: "started", jobId } : m)));
            showBoard(jobId);
          }}
          onCpuProposalDismiss={(messageId) => setMessages((prev) => prev.map((m) => (m.id === messageId && m.kind === "cpu_proposal" ? { ...m, state: "dismissed" } : m)))}
          onShowBoard={showBoard}
          messages={thread}
          health={health}
          needsCode={needsCode}
          codeNotice={rejected ? "Your session was not accepted. Enter the access code again." : null}
          starting={starting}
          startError={startError}
          runStarted={urlRun !== null}
          threaded={threaded}
          currentRun={urlRun}
          runTitle={(id) => recent.find((r) => r.id === id)?.title ?? null}
          canRetry={canRetry}
          versions={versions}
          onPickVersion={(id) => pickVersion(id, true)}
          walkTurnId={walkView && walk ? walk.turnId : null}
          onWalk={startWalk}
          chipsOff={locked || connecting}
          onChip={(chip) => sendSentence(chip, true)}
          onConfirm={(turn, req) => void confirmThreadProposal(turn, req)}
          onDismiss={dismissThreadProposal}
          onRun={(cardId, prompt, req) => void startRun(req, prompt, cardId)}
          onPickYourself={(prompt) => add(pickCard(prompt))}
          onClosest={(prompt, chip: Chip) => add(closestCard(prompt, chip))}
          onExample={fillExample}
          onRetry={readAgain}
          onCode={codeThenAsk}
          onSubmitCode={submitCode}
          scrollKey={`${lastId}:${urlRun ?? ""}:${endKey}:${turns.at(-1)?.id ?? ""}`}
        >
          {cpuJobId && !cpuPanelShown && !thread.some((m) => m.kind === "cpu_choice" && m.jobId === cpuJobId) ? (
            <div className="b-ai">
              <p>Open the board to follow this CPU job.</p>
              <div className="b-acts">
                <button type="button" className="bx ghost sm" aria-expanded={false} aria-controls="bench-panel" onClick={openCpuPanel}>
                  Show board
                </button>
              </div>
            </div>
          ) : null}
          {urlRun && !ended ? (
            <RunningLine
              run={run}
              connection={hasRun ? live.connection : "connecting"}
              wide={wide}
              panelOpen={panelOpen}
              stopping={stopping && active}
              showRef={showRef}
              onShow={showProgress}
              onStop={() => void stop()}
            />
          ) : null}
          {ended ? (
            <EndMessage
              run={run}
              title={title ?? run.title}
              priority={priority}
              liveError={live.error}
              canRerun={entry !== undefined}
              needsCode={needsCode}
              codeNotice={rejected ? "Your session was not accepted. Enter the access code again." : null}
              starting={starting}
              startError={startError}
              panelOpen={panelOpen}
              resultRef={resultRef}
              onOpenPanel={() => {
                setTab((t) => (t === "progress" ? "result" : t));
                setPanelOpen(true);
                setFocusPanel(true);
              }}
              onRerun={rerun}
              onSubmitCode={submitCode}
            />
          ) : null}
          {ended && turns.length > 0 && !markerHere ? (
            <AskTurns
              turns={turns}
              health={health}
              needsCode={needsCode}
              codeNotice={rejected ? "Your session was not accepted. Enter the access code again." : null}
              starting={starting}
              startError={startError}
              onConfirm={(turn, req) => void confirmProposal(turn, req)}
              onDismiss={dismissProposal}
              onNewChip={beginThread}
              onCode={codeThenAskRun}
              onSubmitCode={submitCode}
              versions={versions}
              onPickVersion={(id) => pickVersion(id, true)}
              walkTurnId={walkView && walk ? walk.turnId : null}
              onWalk={startWalk}
            />
          ) : null}
        </ChatThread>
        </>
      )}
      <div className="b-dock">
        <Composer
          text={text}
          onText={setText}
          onSend={send}
          locked={locked}
          busy={parsing || asking || connecting}
          placeholder={
            locked
              ? "Verity is building your chip. Wait for it to finish, or press Stop."
              : askOn
                ? focusVersion
                  ? `Ask about ${focusVersion}, or ask for a change`
                  : "Ask about this run, or ask for a change"
                : urlRun
                  ? "Describe another chip..."
                  : viaAgent && messages.length > 0
                    ? "Ask a question, or describe a chip..."
                    : "Describe a small chip..."
          }
          label={askOn ? "Ask about this run" : undefined}
          busyWord={
            asking || messages.some((m) => m.kind === "parsing" && m.thinking)
              ? "Thinking..."
              : connecting && !parsing
                ? "Connecting..."
                : undefined
          }
          context={askOn && focusVersion ? { label: focusVersion, onRemove: () => setAttached(null) } : null}
          inputRef={inputRef}
        />
        {empty ? <ExamplePrompts onPick={fillExample} onPickYourself={() => add(pickCard(""))} /> : null}
      </div>
    </AppShell>
  );
}
