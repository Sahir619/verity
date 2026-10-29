"use client";

// The only module allowed to talk to the agent (ARCHITECTURE 8.2, 10). The replay path never imports it.
// Everything here treats the network as untrusted: events are validated with zod before they reach the reducer.

import { AGENT_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL, isAccountsConfigured } from "@/lib/env";
import { z } from "zod";
import { RunEventSchema, RunLogSchema } from "@/lib/contracts/schemas";
import { CpuActionSchema, CpuJobViewSchema, CpuKindsSchema, UpgradesResponseSchema, type UpgradesResponse, type CpuGoal, type CpuJobView } from "@/lib/contracts/cpu";
import { ResearchSchema, VERDICTS } from "@/lib/contracts/research";
import { readSteps } from "@/lib/contracts/ask";
import { readOpenTab, readToolSteps } from "@/lib/contracts/agent";
import type { KnobValue } from "@/lib/contracts/presets";
import type { Priority, RunEvent, RunLog } from "@/lib/contracts/types";
import type { LiveHealth, SteerRequest } from "@/lib/contracts/view-types";

export function liveUrl(path: string): string | null {
  return AGENT_URL ? `${AGENT_URL}${path}` : null;
}

export type StartFailureReason =
  | "busy"
  | "budget_exhausted"
  | "session_budget_exhausted"
  | "credits_exhausted"
  | "maintenance"
  | "rate_limited"
  | "unauthorized"
  | "sandbox_down";
export type PostRunResult =
  | { runId: string; capUsd: number | null; streamToken: string | null }
  | { error: string; reason?: StartFailureReason };
/** "unauthorized" is a 401: the session token is missing, invalid or expired. */
export type SendResult = "ok" | "unauthorized" | "failed";

const HEALTH_TIMEOUT_MS = 3000;

/** Said when this session's spend for the UTC day is used up (429 session_budget_exhausted). No figure: the UI never shows a limit. */
const SESSION_SPENT = "You have reached today's spend limit for this session. Try again tomorrow.";
const DAY_SPENT = "Today's live budget is used up.";
const CREDITS_SPENT = "You have used all your run credits. Contact an admin to add more.";

/** The spend-limit sentence for a 429 body's reason, or null for any other reason. */
function spendLimitMessage(body: unknown): string | null {
  const reason = typeof body === "object" && body !== null ? (body as { reason?: unknown }).reason : undefined;
  return reason === "budget_exhausted"
    ? DAY_SPENT
    : reason === "session_budget_exhausted"
    ? SESSION_SPENT
    : reason === "credits_exhausted"
    ? CREDITS_SPENT
    : null;
}


// ---------- session (access code) ----------
// The session token and the per-run stream token live in sessionStorage only: gone when the tab closes, never in the bundle.

const SESSION_KEY = "verity.session";
const STREAM_KEY_PREFIX = "verity.stream.";
const SESSION_EVENT = "verity:session";

type Session = { token: string; expiresAt: string };

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** The stored session, or null when there is none or it has expired (an expired one is removed). */
export function readSession(): Session | null {
  const store = storage();
  const raw = store?.getItem(SESSION_KEY);
  if (!store || !raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<Session>;
    if (typeof parsed.token === "string" && parsed.token && typeof parsed.expiresAt === "string") {
      const expires = Date.parse(parsed.expiresAt);
      if (Number.isFinite(expires) && expires > Date.now()) return { token: parsed.token, expiresAt: parsed.expiresAt };
    }
  } catch {
    // a malformed value is treated as no session
  }
  store.removeItem(SESSION_KEY);
  return null;
}

export function clearSession(): void {
  storage()?.removeItem(SESSION_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

/** For useSyncExternalStore: tells subscribers when the session was stored or cleared. */
export function subscribeSession(onChange: () => void): () => void {
  window.addEventListener(SESSION_EVENT, onChange);
  return () => window.removeEventListener(SESSION_EVENT, onChange);
}

/** The session token as a primitive (stable for useSyncExternalStore), or null. */
export function sessionToken(): string | null {
  return readSession()?.token ?? null;
}

// ---------- accounts (Supabase sign-in) ----------
// Email and password only. There is no sign-up anywhere: the team makes each account in the Supabase dashboard.
// The client is made on first use from a dynamic import, and only when both public env values are set, so the replay path
// and a build without accounts never load it. Only the publishable key is here (public by design); the secret key is the server's.

type AccountClient = import("@supabase/supabase-js").SupabaseClient;
let accountClient: Promise<AccountClient | null> | null = null;

function supabase(): Promise<AccountClient | null> {
  const url = SUPABASE_URL;
  const key = SUPABASE_PUBLISHABLE_KEY;
  if (!isAccountsConfigured || !url || !key) return Promise.resolve(null);
  accountClient ??= import("@supabase/supabase-js")
    .then(({ createClient }) => createClient(url, key, { auth: { detectSessionInUrl: false } }))
    .catch(() => {
      accountClient = null; // a chunk that failed to load is tried again next time
      return null;
    });
  return accountClient;
}

const WRONG_LOGIN = "That email and password do not match.";
const SIGN_IN_OFFLINE = "Verity could not reach the sign-in service. Check your connection and try again.";
const SIGN_IN_BUSY = "Too many attempts. Wait a minute and try again.";

export type SignInResult = { ok: true } | { ok: false; error: string };

/** Supabase signInWithPassword. A wrong login and an unreachable service get different sentences. Never creates an account. */
export async function signIn(email: string, password: string): Promise<SignInResult> {
  const client = await supabase();
  if (!client) return { ok: false, error: SIGN_IN_OFFLINE };
  try {
    const { error } = await client.auth.signInWithPassword({ email, password });
    if (!error) return { ok: true };
    if (!error.status || error.status >= 500 || error.name === "AuthRetryableFetchError") return { ok: false, error: SIGN_IN_OFFLINE };
    return { ok: false, error: error.status === 429 ? SIGN_IN_BUSY : WRONG_LOGIN };
  } catch {
    return { ok: false, error: SIGN_IN_OFFLINE };
  }
}

/** Signs out of this browser only; the same account stays signed in on its other devices. */
export async function signOut(): Promise<void> {
  const client = await supabase();
  try {
    await client?.auth.signOut({ scope: "local" });
  } catch {
    // the session is dropped from this browser either way
  }
}

const emailOf = (user: { email?: string }): string => user.email || "your account";

/** The signed-in account's email, or null. getSession refreshes an expired token first. */
export async function currentAccount(): Promise<string | null> {
  const client = await supabase();
  if (!client) return null;
  try {
    const { data } = await client.auth.getSession();
    return data.session ? emailOf(data.session.user) : null;
  } catch {
    return null;
  }
}

/** Tells `onChange` the account's email (or null) now and whenever it signs in, signs out or changes. Returns the unsubscribe. */
export function onAccountChange(onChange: (email: string | null) => void): () => void {
  let stop = () => {};
  let gone = false;
  void supabase().then((client) => {
    if (!client || gone) return;
    const { data } = client.auth.onAuthStateChange((_event, session) => onChange(session ? emailOf(session.user) : null));
    stop = () => data.subscription.unsubscribe();
  });
  return () => {
    gone = true;
    stop();
  };
}

async function accessToken(): Promise<string | null> {
  const client = await supabase();
  if (!client) return null;
  try {
    const { data } = await client.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

/** The signed-in account's access token when there is one, else the access-code session token as before. */
async function authHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  if (token) return { Authorization: `Bearer ${token}` };
  const session = readSession();
  return session ? { Authorization: `Bearer ${session.token}` } : {};
}

export type SessionResult = { ok: true } | { ok: false; error: string };

/** POST /auth/session { accessCode }. 200 { token, expiresAt }, 401 for a wrong code, 429 when rate limited. */
export async function createSession(accessCode: string): Promise<SessionResult> {
  const url = liveUrl("/auth/session");
  if (!url) return { ok: false, error: "Live runs are offline right now." };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ accessCode }),
    });
    if (res.status === 401) return { ok: false, error: "That access code was not recognized." };
    if (res.status === 429) return { ok: false, error: "Too many attempts. Wait a minute and try again." };
    if (res.ok) {
      const body = (await res.json()) as { token?: string; expiresAt?: string };
      if (typeof body.token === "string" && body.token && typeof body.expiresAt === "string") {
        storage()?.setItem(SESSION_KEY, JSON.stringify({ token: body.token, expiresAt: body.expiresAt }));
        window.dispatchEvent(new Event(SESSION_EVENT));
        return { ok: true };
      }
    }
    return { ok: false, error: "The agent did not respond." };
  } catch {
    return { ok: false, error: "The agent did not respond." };
  }
}

export interface AccountProfile {
  email: string | null;
  role: string;
  credits: number;
  isAdmin: boolean;
  /** Signed in (an account, an access code session or a local admin): every kind of build is open and no credits are taken. A backend that omits it is read through isAdmin. */
  canBuild?: boolean;
  anonymous: boolean;
}

export const PROFILE_EVENT = "verity:profile";

export function dispatchProfileChange(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(PROFILE_EVENT));
  }
}

/** GET /auth/me. Fetches the current session's profile, credits, and admin role. */
export async function getAccountProfile(): Promise<AccountProfile | null> {
  const url = liveUrl("/auth/me");
  if (!url) return null;
  try {
    const headers = await authHeaders();
    const res = await fetch(url, { headers: { ...headers, Accept: "application/json" } });
    if (!res.ok) return null;
    return (await res.json()) as AccountProfile;
  } catch {
    return null;
  }
}

export type AdminCreateUserResult =
  | { ok: true; email: string; credits: number; userId: string }
  | { ok: false; error: string };

/** POST /admin/users. Admin creates a new user account with initial credits. */
export async function createAdminUser(email: string, password: string, credits: number = 3): Promise<AdminCreateUserResult> {
  const url = liveUrl("/admin/users");
  if (!url) return { ok: false, error: "The agent is offline." };
  try {
    const headers = await authHeaders();
    const res = await fetch(url, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email, password, credits }),
    });
    const body = (await res.json().catch(() => ({}))) as { error?: string; email?: string; credits?: number; userId?: string };
    if (!res.ok) {
      return { ok: false, error: typeof body.error === "string" ? body.error : "Could not create user." };
    }
    return {
      ok: true,
      email: body.email ?? email,
      credits: typeof body.credits === "number" ? body.credits : credits,
      userId: body.userId ?? "",
    };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}


export interface AdminUserUsage {
  owner: string;
  credits: number;
  email: string | null;
  role: string;
  runsCount: number;
  totalSpentUsd: number;
  createdAt: string | null;
  updatedAt: string | null;
}

export type AdminListUsersResult = { ok: true; users: AdminUserUsage[] } | { ok: false; error: string };

/** GET /admin/users (admin only): every account, newest first. */
export async function listAdminUsers(): Promise<AdminListUsersResult> {
  const url = liveUrl("/admin/users");
  if (!url) return { ok: false, error: "The agent is offline." };
  try {
    const res = await fetch(url, { headers: { Accept: "application/json", ...(await authHeaders()) } });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      return { ok: false, error: typeof body.error === "string" ? body.error : "Could not load users." };
    }
    const body = (await res.json()) as { users?: AdminUserUsage[] };
    return { ok: true, users: Array.isArray(body.users) ? body.users : [] };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}

export type AdminSetCreditsResult = { ok: true; owner: string; credits: number } | { ok: false; error: string };

/** POST /admin/credits (admin only). Pass either `credits` (set to this) or `addCredits` (add to the current balance). */
export async function setAdminCredits(
  target: { owner?: string; email?: string },
  change: { credits?: number; addCredits?: number },
): Promise<AdminSetCreditsResult> {
  const url = liveUrl("/admin/credits");
  if (!url) return { ok: false, error: "The agent is offline." };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ ...target, ...change }),
    });
    const body = (await res.json().catch(() => ({}))) as { error?: string; owner?: string; credits?: number };
    if (!res.ok) {
      return { ok: false, error: typeof body.error === "string" ? body.error : "Could not update credits." };
    }
    return { ok: true, owner: body.owner ?? target.owner ?? "", credits: typeof body.credits === "number" ? body.credits : 0 };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}

export type AdminRunsResult = { ok: true; runs: MyRun[] } | { ok: false; error: string };

/** GET /admin/runs?owner=|email= (admin only): one user's runs, same shape as GET /runs/mine. */
export async function listAdminRuns(target: { owner?: string; email?: string }): Promise<AdminRunsResult> {
  const qs = new URLSearchParams(
    target.owner ? { owner: target.owner } : target.email ? { email: target.email } : {},
  ).toString();
  const url = liveUrl(`/admin/runs${qs ? `?${qs}` : ""}`);
  if (!url) return { ok: false, error: "The agent is offline." };
  try {
    const res = await fetch(url, { headers: { Accept: "application/json", ...(await authHeaders()) } });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      return { ok: false, error: typeof body.error === "string" ? body.error : "Could not load runs." };
    }
    const body = (await res.json()) as { runs?: MyRun[] };
    return { ok: true, runs: Array.isArray(body.runs) ? body.runs : [] };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}

// ---------- health, runs, steering ----------

/** GET /health (no auth). Never throws: any failure is reported as the "error" availability state. */
export async function checkHealth(signal?: AbortSignal): Promise<LiveHealth> {
  const url = liveUrl("/health");
  if (!url) return { status: "not_configured", capUsd: null };
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), HEALTH_TIMEOUT_MS);
  signal?.addEventListener("abort", () => timeout.abort(), { once: true });
  try {
    const res = await fetch(url, { signal: timeout.signal, headers: { Accept: "application/json" } });
    if (!res.ok) return { status: "error", capUsd: null };
    const body = (await res.json()) as {
      liveEnabled?: boolean; reason?: string; capUsd?: number; authRequired?: boolean; features?: unknown;
    };
    const capUsd = typeof body.capUsd === "number" ? body.capUsd : null;
    const authRequired = body.authRequired === true;
    const features = Array.isArray(body.features) ? body.features.filter((f): f is string => typeof f === "string") : [];
    if (body.liveEnabled) return { status: "ready", capUsd, authRequired, features };
    if (body.reason === "budget_exhausted") return { status: "budget_exhausted", capUsd, authRequired, features };
    if (body.reason === "busy") return { status: "busy", capUsd, authRequired, features };
    if (body.reason === "sandbox_down") return { status: "sandbox_down", capUsd, authRequired, features };
    return { status: "error", capUsd, authRequired, features };
  } catch {
    return { status: "error", capUsd: null };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * POST /runs. 201 { runId, capUsd, streamToken }, 401 (no or expired session), 422 { error }, 429 { reason } or 503 { reason: "sandbox_down" }.
 * `params` (bench knobs) is sent only when given. Callers pass it only when /health reports the "params" feature,
 * because an older agent ignores unknown fields and would quietly run the default chip. `researchId` (the check made
 * before Run) is sent only when given; the agent checks that it is this session's and fits the chip, and ignores it if not.
 * `threadId` (the chat thread the run starts from, when the agent has the "agent" feature) goes in the body when given, so
 * the agent can write the run into that thread's facts.
 */
export async function postRun(
  presetId: string,
  priority: Priority,
  params?: Record<string, KnobValue>,
  researchId?: string,
  threadId?: string,
): Promise<PostRunResult> {
  const url = liveUrl("/runs");
  if (!url) return { error: "Live runs are offline right now." };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ presetId, priority, ...(params ? { params } : {}), ...(researchId ? { researchId } : {}), ...(threadId ? { threadId } : {}) }),
    });
    if (res.status === 201) {
      const body = (await res.json()) as { runId?: string; capUsd?: number; streamToken?: string };
      if (typeof body.runId === "string" && body.runId) {
        const streamToken = typeof body.streamToken === "string" && body.streamToken ? body.streamToken : null;
        if (streamToken) storage()?.setItem(STREAM_KEY_PREFIX + body.runId, streamToken);
        dispatchProfileChange();
        return { runId: body.runId, capUsd: typeof body.capUsd === "number" ? body.capUsd : null, streamToken };

      }
      return { error: "The agent did not respond." };
    }
    if (res.status === 401) {
      clearSession();
      return { error: "Enter the access code to start a live run.", reason: "unauthorized" };
    }
    if (res.status === 429) {
      const body = (await res.json().catch(() => ({}))) as { reason?: string };
      const reason: StartFailureReason =
        body.reason === "budget_exhausted" ||
        body.reason === "session_budget_exhausted" ||
        body.reason === "credits_exhausted" ||
        body.reason === "maintenance" ||
        body.reason === "busy" ||
        body.reason === "rate_limited"
          ? body.reason
          : "busy";
      const message =
        reason === "busy"
          ? "Another live run is in progress."
          : reason === "rate_limited"
            ? "This session has used its live runs for now. Wait a while, or watch a recorded run."
            : (spendLimitMessage(body) ?? "Live runs are paused right now.");
      return { error: message, reason };
    }
    if (res.status === 422) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      return { error: typeof body.error === "string" && body.error ? body.error : "The agent did not accept this chip." };
    }
    if (res.status === 503) {
      // The agent is up but its test machine (Docker or the sandbox image) is not. Any other 503 stays "did not respond".
      // The agent's own sentence says which it is (Docker stopped, or the tool image missing); the fallback stays neutral.
      const body = (await res.json().catch(() => ({}))) as { reason?: string; error?: unknown };
      if (body.reason === "sandbox_down") {
        const said = typeof body.error === "string" ? body.error.trim() : "";
        return { error: said || "Live runs are offline: the test machine is not ready.", reason: "sandbox_down" };
      }
    }
    return { error: "The agent did not respond." };
  } catch {
    return { error: "The agent did not respond." };
  }
}

export type ResumeRunResult = { runId: string; status: string } | { error: string; reason?: StartFailureReason };

/**
 * POST /runs/{id}/resume (owner only): continues a run stopped at a checkpoint (credits exhausted, a network
 * problem, or a sandbox problem). Stores the fresh stream token like `postRun`, so navigating to
 * `/bench?run=<id>` right after picks the live stream back up.
 */
export async function resumeRun(runId: string): Promise<ResumeRunResult> {
  const url = liveUrl(`/runs/${encodeURIComponent(runId)}/resume`);
  if (!url) return { error: "Live runs are offline right now." };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { Accept: "application/json", ...(await authHeaders()) },
    });
    if (res.ok) {
      const body = (await res.json()) as { runId?: string; status?: string; streamToken?: string };
      const streamToken = typeof body.streamToken === "string" && body.streamToken ? body.streamToken : null;
      if (streamToken) storage()?.setItem(STREAM_KEY_PREFIX + runId, streamToken);
      dispatchProfileChange();
      return { runId: typeof body.runId === "string" && body.runId ? body.runId : runId, status: body.status ?? "queued" };
    }
    if (res.status === 401) {
      clearSession();
      return { error: "Sign in to resume this run.", reason: "unauthorized" };
    }
    if (res.status === 403) return { error: "This run belongs to another account." };
    if (res.status === 404) return { error: "This run is no longer on the server." };
    if (res.status === 409) return { error: "This run cannot be resumed right now." };
    if (res.status === 429) {
      const body = (await res.json().catch(() => ({}))) as { reason?: string };
      if (body.reason === "credits_exhausted") {
        return { error: "You have used all your run credits. Contact an admin to add more.", reason: "credits_exhausted" };
      }
      return { error: "Another live run is in progress.", reason: "busy" };
    }
    return { error: "The agent did not respond." };
  } catch {
    return { error: "Could not reach the server." };
  }
}

// ---------- plain-English parse (bench, spec 5.2) ----------

// The fact check on a first message. Every field is optional and never fails the reply: an older agent sends none of them,
// and a value this bench does not know reads as absent, so the reply degrades to the plain card or refusal.
export { VERDICTS, type Verdict, type Research, type ResearchSource } from "@/lib/contracts/research";

const CheckShape = {
  cpuKinds: CpuKindsSchema.optional(),
  verdict: z.enum(VERDICTS).optional().catch(undefined),
  intentClass: z.enum(["not_chip", "question", "not_digital", "too_big", "in_family", "near_family", "unsure"]).optional().catch(undefined),
  research: ResearchSchema.nullable().optional().catch(undefined),
  // The knob ids the sentence actually stated; every other knob took its default. Absent from an older agent.
  statedKnobs: z.array(z.string()).optional().catch(undefined),
};

const ParseReplySchema = z.discriminatedUnion("result", [
  z.object({ result: z.literal("in_range"), family: z.string(), params: z.record(z.string(), z.unknown()), note: z.string().optional(), ...CheckShape }),
  z.object({
    result: z.literal("out_of_range"),
    reason: z.string(),
    closest: z.object({ family: z.string(), params: z.record(z.string(), z.unknown()) }).nullable(),
    ...CheckShape,
  }),
]);
export type ParseReply = z.infer<typeof ParseReplySchema>;
export type ParseResult =
  | { ok: true; reply: ParseReply }
  | { ok: false; reason: "unavailable" | "unauthorized" | "rate_limited" | "spend_limit" | "failed"; error: string };

export type CpuJobResult = { ok: true; job: CpuJobView } | { ok: false; error: string };

/** CPU requests use the same authenticated live boundary as ordinary runs. */
async function cpuJobRequest(path: string, body?: { specId: "rv32i_micro"; goal: CpuGoal; threadId?: string } | { upgradeId: string } | Record<string, never>, signal?: AbortSignal): Promise<CpuJobResult> {
  const url = liveUrl(path);
  if (!url) return { ok: false, error: "Live runs are off on this site." };
  const ac = new AbortController();
  const abort = () => ac.abort();
  if (signal?.aborted) ac.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 8000);
  try {
    const headers = await authHeaders();
    const res = await fetch(url, {
      method: body ? "POST" : "GET", signal: ac.signal,
      headers: { ...headers, Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (res.status === 403) {
      // The server says "Sign in to build a CPU." to a visitor with no session; a signed-in one is refused only another account's job.
      const said = ((await res.json().catch(() => null)) as { error?: unknown } | null)?.error;
      const signIn = typeof said !== "string" || said === "Sign in to build a CPU.";
      return { ok: false, error: signIn ? "Sign in to build a CPU." : "This CPU job belongs to another account." };
    }
    if (res.status === 401) return { ok: false, error: "Your session was not accepted. Sign in again." };
    if (res.status === 404) return { ok: false, error: "This CPU job is not available." };
    const value: unknown = await res.json().catch(() => null);
    if (!res.ok) {
      const reason = typeof value === "object" && value !== null ? (value as { reason?: unknown; code?: unknown }).reason ?? (value as { code?: unknown }).code : null;
      // A 409 carries the server's plain reason (for example, the parent has not passed).
      const plain = res.status === 409 && typeof value === "object" && value !== null ? (value as { error?: unknown }).error : null;
      if (typeof plain === "string" && plain.trim()) return { ok: false, error: plain.trim() };
      return { ok: false, error: res.status === 402 || reason === "no_credits" ? CREDITS_SPENT : spendLimitMessage(value) ?? "The CPU job request failed. Try again." };
    }
    const parsed = CpuJobViewSchema.safeParse(value);
    return parsed.success ? { ok: true, job: parsed.data } : { ok: false, error: "The server returned an unreadable CPU job. Try again." };
  } catch {
    return { ok: false, error: "Could not reach the CPU job. Try again." };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function postCpuJob(goal: CpuGoal, threadId?: string): Promise<CpuJobResult> {
  return cpuJobRequest("/cpu-jobs", { specId: "rv32i_micro", goal, ...(threadId ? { threadId } : {}) });
}

export function getCpuJob(id: string, signal?: AbortSignal): Promise<CpuJobResult> {
  return cpuJobRequest(`/cpu-jobs/${encodeURIComponent(id)}`, undefined, signal);
}

/** GET /cpu-jobs/mine: this session's CPU jobs (an account's on every device), newest first. An empty list when off or unreadable. */
export async function listMyCpuJobs(signal?: AbortSignal): Promise<CpuJobView[]> {
  const url = liveUrl("/cpu-jobs/mine");
  if (!url) return [];
  try {
    const headers = await authHeaders();
    const res = await fetch(url, { signal, headers: { ...headers, Accept: "application/json" } });
    if (!res.ok) return [];
    const parsed = z.object({ jobs: z.array(CpuJobViewSchema) }).safeParse(await res.json().catch(() => null));
    return parsed.success ? parsed.data.jobs : [];
  } catch {
    return [];
  }
}

export type CpuUpgradesResult = { ok: true; advice: UpgradesResponse } | { ok: false; error: string };

/** Research can take longer than a board poll. Abort on unmount and cap the total wait. */
export async function getCpuUpgrades(id: string, signal?: AbortSignal): Promise<CpuUpgradesResult> {
  const url = liveUrl(`/cpu-jobs/${encodeURIComponent(id)}/upgrades`);
  if (!url) return { ok: false, error: "Live advice is off on this site." };
  const ac = new AbortController();
  const abort = () => ac.abort();
  if (signal?.aborted) ac.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 120000);
  try {
    const res = await fetch(url, { signal: ac.signal, headers: { Accept: "application/json", ...(await authHeaders()) } });
    if (!res.ok) return { ok: false, error: res.status === 403 ? "This CPU advice belongs to another account." : "CPU advice is unavailable. Try again." };
    const parsed = UpgradesResponseSchema.safeParse(await res.json());
    return parsed.success ? { ok: true, advice: parsed.data } : { ok: false, error: "The server returned unreadable CPU advice." };
  } catch {
    return { ok: false, error: "Could not load CPU advice. Try again." };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

/** Starts an upgrade job from a passed CPU job. 202 answers with the new job, 409 with a plain reason. */
export function postCpuUpgrade(id: string, upgradeId: string): Promise<CpuJobResult> {
  return cpuJobRequest(`/cpu-jobs/${encodeURIComponent(id)}/upgrade`, { upgradeId });
}

/** Starts a core retry of a failed CPU job whose seven blocks passed. 202 answers with the new job, 409 with a plain reason. */
export function postCpuRetry(id: string): Promise<CpuJobResult> {
  return cpuJobRequest(`/cpu-jobs/${encodeURIComponent(id)}/retry`, {});
}

/**
 * POST /parse { text }. The agent (Nemotron) maps one sentence onto a family and knobs, or refuses with the closest
 * buildable chip. The reply is validated here and again against the knob table by the caller; it never bypasses either.
 * 404 or 405 means this agent has no parse endpoint yet.
 */
export async function postParse(text: string): Promise<ParseResult> {
  const url = liveUrl("/parse");
  if (!url) return { ok: false, reason: "unavailable", error: "Live runs are offline right now." };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ text }),
    });
    if (res.status === 404 || res.status === 405) {
      return { ok: false, reason: "unavailable", error: "Plain-English parse lands with the backend. Use the knobs below for now." };
    }
    if (res.status === 401) {
      clearSession();
      return { ok: false, reason: "unauthorized", error: "Enter the access code first, then ask again." };
    }
    if (res.status === 429) {
      const body: unknown = await res.json().catch(() => null);
      const spent = spendLimitMessage(body);
      // A spend limit also refuses Run, so the caller must not offer the manual path; a plain rate limit does not.
      if (spent) return { ok: false, reason: "spend_limit", error: spent };
      return { ok: false, reason: "rate_limited", error: "Too many requests. Wait a minute and try again." };
    }
    if (!res.ok) return { ok: false, reason: "failed", error: "The agent could not read that request. Use the knobs below." };
    const parsed = ParseReplySchema.safeParse(await res.json());
    if (!parsed.success) {
      if (process.env.NODE_ENV !== "production") console.warn("[verity] parse reply rejected by schema", parsed.error.issues);
      return { ok: false, reason: "failed", error: "The agent sent an answer the bench could not check. Use the knobs below." };
    }
    return { ok: true, reply: parsed.data };
  } catch {
    return { ok: false, reason: "failed", error: "The agent did not respond." };
  }
}

// ---------- ask about a finished run (bench, design partner claim 3) ----------

const ProposalSchema = z.object({
  presetId: z.string(),
  params: z.record(z.string(), z.union([z.number(), z.boolean(), z.string()])),
  priority: z.enum(["area", "speed", "power"]),
  mode: z.string().optional(),
  title: z.string(),
  idea: z.string().nullable().optional(),
  lastRunUsd: z.number().nullable().optional(),
  capUsd: z.number(),
});
const AskReplySchema = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("answer"),
    text: z.string(),
    bullets: z.array(z.string()).default([]),
    basis: z.string().default(""),
    templated: z.boolean().default(false),
    // "Walk me through it": a walkthrough built by code, absent from an older agent.
    steps: z.unknown().optional().transform(readSteps),
  }),
  z.object({ kind: z.literal("proposal"), text: z.string(), proposal: ProposalSchema }),
  z.object({ kind: z.literal("new_chip"), text: z.string() }),
  z.object({ kind: z.literal("refused"), text: z.string() }),
]);
export type AskReply = z.infer<typeof AskReplySchema>;
export type Proposal = z.infer<typeof ProposalSchema>;
export type AskResult =
  | { ok: true; reply: AskReply }
  | { ok: false; reason: "unavailable" | "unauthorized" | "still_running" | "rate_limited" | "not_yours" | "timeout" | "aborted" | "failed"; error: string };

const ASK_TIMEOUT_MS = 60_000;

/**
 * POST /runs/{id}/ask { text, focusVersion? }. Owner session only, and only after the run has ended (409 while it is queued
 * or running). One answer comes back whole: the run's own data in words, a proposed new run, a pointer to a new chip, or a
 * plain refusal. Gives up after a minute, or when the caller aborts (the reader moved to another run).
 */
export async function postAsk(runId: string, text: string, focusVersion?: string, signal?: AbortSignal): Promise<AskResult> {
  const url = liveUrl(`/runs/${encodeURIComponent(runId)}/ask`);
  if (!url) return { ok: false, reason: "unavailable", error: "Live runs are offline right now." };
  const timeout = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    timeout.abort();
  }, ASK_TIMEOUT_MS);
  signal?.addEventListener("abort", () => timeout.abort(), { once: true });
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(await authHeaders()) },
      body: JSON.stringify(focusVersion ? { text, focusVersion } : { text }),
      signal: timeout.signal,
    });
    if (res.status === 404 || res.status === 405) {
      return { ok: false, reason: "unavailable", error: "This server cannot answer questions about a run yet." };
    }
    if (res.status === 401) {
      clearSession();
      return { ok: false, reason: "unauthorized", error: "Your session ended. Enter the access code, then ask again." };
    }
    if (res.status === 403) return { ok: false, reason: "not_yours", error: "This run belongs to another session, so only its owner can ask about it." };
    if (res.status === 409) return { ok: false, reason: "still_running", error: "This run is still going. Ask again when it ends." };
    if (res.status === 429) {
      const body: unknown = await res.json().catch(() => null);
      return {
        ok: false,
        reason: "rate_limited",
        error: spendLimitMessage(body) ?? "Too many questions for now. Wait a minute and try again.",
      };
    }
    if (!res.ok) return { ok: false, reason: "failed", error: "I could not answer that just now." };
    const parsed = AskReplySchema.safeParse(await res.json().catch(() => null));
    if (!parsed.success) {
      if (process.env.NODE_ENV !== "production") console.warn("[verity] ask reply rejected by schema", parsed.error.issues);
      return { ok: false, reason: "failed", error: "The agent sent an answer the bench could not check." };
    }
    return { ok: true, reply: parsed.data };
  } catch {
    if (timedOut) return { ok: false, reason: "timeout", error: "That took too long. Try asking again." };
    if (signal?.aborted) return { ok: false, reason: "aborted", error: "" };
    return { ok: false, reason: "failed", error: "I could not reach Verity's server, so I could not answer that." };
  } finally {
    clearTimeout(timer);
  }
}

// ---------- one message to the chip engineer agent (bench, "agent" feature) ----------

// What the agent did first and what it offers next ride on the same reply. Each is optional and read one by one: a step,
// a tab or a source this bench cannot read is dropped on its own and never fails the reply.
const AgentReplySchema = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("answer"),
    text: z.string(),
    bullets: z.array(z.string()).default([]),
    basis: z.string().default(""),
    templated: z.boolean().default(false),
    steps: z.unknown().optional().transform(readSteps),
    toolSteps: z.unknown().optional().transform(readToolSteps),
    chips: z.array(z.string()).optional().catch(undefined),
    openTab: z.unknown().optional().transform(readOpenTab),
    research: ResearchSchema.nullable().optional().catch(undefined),
  }),
  z.object({ kind: z.literal("proposal"), text: z.string(), proposal: ProposalSchema, toolSteps: z.unknown().optional().transform(readToolSteps) }),
  z.object({ kind: z.literal("refused"), text: z.string(), toolSteps: z.unknown().optional().transform(readToolSteps) }),
  z.object({ kind: z.literal("cpu_proposal"), text: z.string(), action: CpuActionSchema, toolSteps: z.unknown().optional().transform(readToolSteps) }),
]);
export type AgentReply = z.infer<typeof AgentReplySchema>;
const MessageResponseSchema = z.object({
  threadId: z.string(),
  // The fact check, exactly the POST /parse reply, only when this message ran it (the first of a thread).
  check: ParseReplySchema.nullable().optional(),
  // Null only when the check says the message is not a chip.
  reply: AgentReplySchema.nullable().optional(),
});
export type MessageResult =
  | { ok: true; check: ParseReply | null; reply: AgentReply | null }
  | { ok: false; reason: "unavailable" | "unauthorized" | "rate_limited" | "spend_limit" | "not_yours" | "still_running" | "timeout" | "aborted" | "failed"; error: string };

// The first message of a thread also looks up sources and may use several tools, so it gets longer than an ask. The agent
// part of a message stops at 90 s on the server, so a slow message normally comes back (answered from code) before this.
const MESSAGE_TIMEOUT_MS = 120_000;

/**
 * POST /threads/{threadId}/messages { text, runId?, focusVersion?, cpuJobId? }. `cpuJobId` is the CPU job whose board is on
 * screen (the ?cpuJob= id), so the agent knows what the reader is looking at. One answer comes back whole: the fact check when this
 * message ran it, and the agent's reply (an answer, a proposed run, or a refusal) with the tools it used. A spend limit is
 * `spend_limit`, as on POST /parse. 403 and 404 mean the run is not this session's; 409 means it is still going. Gives up
 * after two minutes, or when the caller aborts (the reader moved to another thread).
 */
export async function postMessage(threadId: string, text: string, runId?: string, focusVersion?: string, signal?: AbortSignal, cpuJobId?: string): Promise<MessageResult> {
  const url = liveUrl(`/threads/${encodeURIComponent(threadId)}/messages`);
  if (!url) return { ok: false, reason: "unavailable", error: "Live runs are offline right now." };
  const timeout = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    timeout.abort();
  }, MESSAGE_TIMEOUT_MS);
  signal?.addEventListener("abort", () => timeout.abort(), { once: true });
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ text, ...(runId ? { runId } : {}), ...(focusVersion ? { focusVersion } : {}), ...(cpuJobId ? { cpuJobId } : {}) }),
      signal: timeout.signal,
    });
    if (res.status === 401) {
      clearSession();
      return { ok: false, reason: "unauthorized", error: "Your session ended. Enter the access code, then send that again." };
    }
    if (res.status === 403 || (res.status === 404 && runId)) {
      return { ok: false, reason: "not_yours", error: "This run belongs to another session, so only its owner can ask about it." };
    }
    if (res.status === 409) return { ok: false, reason: "still_running", error: "This run is still going. Ask again when it ends." };
    if (res.status === 404 || res.status === 405) return { ok: false, reason: "unavailable", error: "This server cannot answer messages yet." };
    if (res.status === 422) return { ok: false, reason: "failed", error: "I could not read that message." };
    if (res.status === 429) {
      const body: unknown = await res.json().catch(() => null);
      const spent = spendLimitMessage(body);
      if (spent) return { ok: false, reason: "spend_limit", error: spent };
      return { ok: false, reason: "rate_limited", error: "Too many messages for now. Wait a minute and try again." };
    }
    if (!res.ok) return { ok: false, reason: "failed", error: "I could not answer that just now." };
    const parsed = MessageResponseSchema.safeParse(await res.json().catch(() => null));
    if (!parsed.success || (!parsed.data.check && !parsed.data.reply)) {
      if (process.env.NODE_ENV !== "production" && !parsed.success) console.warn("[verity] message reply rejected by schema", parsed.error.issues);
      return { ok: false, reason: "failed", error: "The agent sent an answer the bench could not check." };
    }
    return { ok: true, check: parsed.data.check ?? null, reply: parsed.data.reply ?? null };
  } catch {
    if (timedOut) return { ok: false, reason: "timeout", error: "That took too long. Try sending it again." };
    if (signal?.aborted) return { ok: false, reason: "aborted", error: "" };
    return { ok: false, reason: "failed", error: "I could not reach Verity's server, so I could not answer that." };
  } finally {
    clearTimeout(timer);
  }
}

// ---------- the runs this server knows (workspace page) ----------

// GET /runs, public. `outcome` comes from the server's run_outcome table: ratios against that run's own v1 (1 = same as v1,
// lower is better, null when unknown), the versions tried, the run's actual spend, and the sign-off seeds when a sheet exists.
// A run that ended before outcomes were kept has none. A field this page cannot read becomes absent; it never fails the list.
const ratio = z.number().nullable().catch(null);
const OutcomeSchema = z.object({
  areaVsV1: ratio,
  clockVsV1: ratio,
  powerVsV1: ratio,
  versions: z.number().catch(0),
  usd: z.number().catch(0),
  signoff: z.object({ seedsPassed: z.number(), seedsRun: z.number() }).optional().catch(undefined),
});
const ServerRunSchema = z.object({
  id: z.string().min(1),
  presetId: z.string(),
  title: z.string(),
  status: z.string(),
  finishedAt: z.string().nullable().optional().catch(null),
  winner: z.object({ label: z.string() }).nullable().optional().catch(null),
  outcome: OutcomeSchema.optional().catch(undefined),
});
export type ServerRun = z.infer<typeof ServerRunSchema>;
export type RunOutcome = NonNullable<ServerRun["outcome"]>;
export type RunsResult = { ok: true; runs: ServerRun[] } | { ok: false; reason: "unavailable" | "failed" };

const RUNS_TIMEOUT_MS = 8000;

/** GET /runs?limit=100, newest first. Never throws: any failure comes back as `ok: false` and the page says so. */
export async function listRuns(signal?: AbortSignal): Promise<RunsResult> {
  const url = liveUrl("/runs?limit=100");
  if (!url) return { ok: false, reason: "unavailable" };
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), RUNS_TIMEOUT_MS);
  signal?.addEventListener("abort", () => timeout.abort(), { once: true });
  try {
    const res = await fetch(url, { signal: timeout.signal, headers: { Accept: "application/json" } });
    if (!res.ok) return { ok: false, reason: "failed" };
    const body: unknown = await res.json().catch(() => null);
    const items = typeof body === "object" && body !== null ? (body as { runs?: unknown }).runs : undefined;
    if (!Array.isArray(items)) return { ok: false, reason: "failed" };
    const runs: ServerRun[] = [];
    for (const item of items) {
      const parsed = ServerRunSchema.safeParse(item);
      if (parsed.success) runs.push(parsed.data);
    }
    return { ok: true, runs };
  } catch {
    return { ok: false, reason: "failed" };
  } finally {
    clearTimeout(timer);
  }
}

// ---------- the signed-in account's chats and runs (sidebar) ----------

// GET /threads, GET /threads/{id} and GET /runs/mine answer for the caller only, so each carries the caller's token. A field
// this bench cannot read becomes absent; one damaged item is dropped on its own and never fails the list.
const ThreadEntrySchema = z.object({
  id: z.string().min(1),
  title: z.string().catch(""),
  updatedAt: z.string().catch(""),
  runIds: z.array(z.string()).catch([]),
});
export type ThreadEntry = z.infer<typeof ThreadEntrySchema>;

const MyRunSchema = z.object({
  id: z.string().min(1),
  title: z.string().catch(""),
  threadId: z.string().nullable().optional().catch(null),
  createdAt: z.string().catch(""),
  status: z.string().nullable().optional().catch(null),
});
export type MyRun = z.infer<typeof MyRunSchema>;

// One stored turn. `reply` and `check` are the wire objects of a live reply (the AgentReply and the ParseReply) and are on
// turns stored after they were kept; an older turn carries only its text and its step line.
const ThreadTurnSchema = z.object({
  seq: z.number(),
  role: z.enum(["user", "verity"]),
  text: z.string(),
  toolSteps: z.unknown().optional().transform(readToolSteps),
  reply: AgentReplySchema.nullable().optional().catch(undefined),
  check: ParseReplySchema.nullable().optional().catch(undefined),
  createdAt: z.string().catch(""),
});
export type ThreadTurn = z.infer<typeof ThreadTurnSchema>;
export type ThreadView = { threadId: string; runIds: string[]; turns: ThreadTurn[] };

export type ReadResult<T> = { ok: true; value: T } | { ok: false; reason: "unavailable" | "unauthorized" | "not_found" | "failed" };

const READ_TIMEOUT_MS = 8000;

/** One GET with the caller's token. Never throws: `status` is 0 when the request did not complete (offline, timeout, aborted). */
async function getJson(path: string, signal?: AbortSignal): Promise<{ status: number; body: unknown } | null> {
  const url = liveUrl(path);
  if (!url) return null;
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), READ_TIMEOUT_MS);
  signal?.addEventListener("abort", () => timeout.abort(), { once: true });
  try {
    const res = await fetch(url, { signal: timeout.signal, headers: { Accept: "application/json", ...(await authHeaders()) } });
    return { status: res.status, body: res.ok ? await res.json().catch(() => null) : null };
  } catch {
    return { status: 0, body: null };
  } finally {
    clearTimeout(timer);
  }
}

function readFailure(got: { status: number } | null): { ok: false; reason: "unavailable" | "unauthorized" | "not_found" | "failed" } {
  if (!got) return { ok: false, reason: "unavailable" };
  if (got.status === 401) return { ok: false, reason: "unauthorized" };
  if (got.status === 404) return { ok: false, reason: "not_found" };
  if (got.status === 405) return { ok: false, reason: "unavailable" };
  return { ok: false, reason: "failed" };
}

function listOf<T>(body: unknown, key: string, schema: z.ZodType<T>): T[] | null {
  const items = typeof body === "object" && body !== null ? (body as Record<string, unknown>)[key] : undefined;
  if (!Array.isArray(items)) return null;
  const out: T[] = [];
  for (const item of items) {
    const parsed = schema.safeParse(item);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}

/** GET /threads: this account's chats, newest first (at most 50). */
export async function listThreads(signal?: AbortSignal): Promise<ReadResult<ThreadEntry[]>> {
  const got = await getJson("/threads", signal);
  if (!got || got.status !== 200) return readFailure(got);
  const threads = listOf(got.body, "threads", ThreadEntrySchema);
  return threads ? { ok: true, value: threads } : { ok: false, reason: "failed" };
}

/** GET /runs/mine: the runs this account started, newest first (at most 50). */
export async function listMyRuns(signal?: AbortSignal): Promise<ReadResult<MyRun[]>> {
  const got = await getJson("/runs/mine", signal);
  if (!got || got.status !== 200) return readFailure(got);
  const runs = listOf(got.body, "runs", MyRunSchema);
  return runs ? { ok: true, value: runs } : { ok: false, reason: "failed" };
}

/** GET /threads/{id}: every stored turn of one chat, oldest first, with the reply and the check where they were kept. */
export async function readThread(threadId: string, signal?: AbortSignal): Promise<ReadResult<ThreadView>> {
  const got = await getJson(`/threads/${encodeURIComponent(threadId)}`, signal);
  if (!got || got.status !== 200) return readFailure(got);
  const body = got.body as { threadId?: unknown; runIds?: unknown } | null;
  const turns = listOf(got.body, "turns", ThreadTurnSchema);
  if (!body || typeof body.threadId !== "string" || !turns) return { ok: false, reason: "failed" };
  const runIds = Array.isArray(body.runIds) ? body.runIds.filter((r): r is string => typeof r === "string") : [];
  return { ok: true, value: { threadId: body.threadId, runIds, turns } };
}

async function postJson(path: string, body?: unknown): Promise<SendResult> {
  const url = liveUrl(path);
  if (!url) return "failed";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401) {
      clearSession();
      return "unauthorized";
    }
    return res.ok ? "ok" : "failed";
  } catch {
    return "failed";
  }
}

/**
 * POST /runs/{id}/steer (202). Pin, toggle and winner_override are echoed as a human event, which clears their pending
 * state. Gate steers and autopilot are not echoed (the result shows up as a gate event) and are never tracked as pending.
 */
export function postSteer(runId: string, req: SteerRequest): Promise<SendResult> {
  return postJson(`/runs/${encodeURIComponent(runId)}/steer`, req);
}

/** POST /runs/{id}/stop (202), then the server emits a run event with phase "stopped". */
export function postStop(runId: string): Promise<SendResult> {
  return postJson(`/runs/${encodeURIComponent(runId)}/stop`);
}

export type StreamHandlers = {
  onOpen(): void;
  onEvent(e: RunEvent): void;
  /** `closed` is true when the browser gave up on its own (readyState CLOSED). */
  onError(closed: boolean): void;
};

/**
 * GET /runs/{id}/log with this account's or session's sign-in, for the run view inside the CPU panel: a run that is still
 * going is readable only by the session that started it. The log passes the same schema as a recorded run. The HTTP status
 * is kept in brackets in the error.
 */
export async function loadLiveRunLog(runId: string): Promise<{ ok: true; log: RunLog } | { ok: false; error: string }> {
  const url = liveUrl(`/runs/${encodeURIComponent(runId)}/log`);
  if (!url) return { ok: false, error: "No agent is configured." };
  try {
    const res = await fetch(url, { headers: { Accept: "application/json", ...(await authHeaders()) } });
    if (!res.ok) return { ok: false, error: `Could not load the run log (${res.status})` };
    const parsed = RunLogSchema.safeParse(await res.json());
    if (!parsed.success) return { ok: false, error: "The run log could not be read." };
    return { ok: true, log: parsed.data as RunLog };
  } catch {
    return { ok: false, error: "Could not load the run log" };
  }
}

/**
 * POST /runs/{id}/stream-token (owner only): a fresh stream token for a run this account or session started, for a tab or
 * device that never saw the 201 of POST /runs. Kept in sessionStorage like the first one. null when the server refuses.
 */
async function fetchStreamToken(runId: string): Promise<string | null> {
  const url = liveUrl(`/runs/${encodeURIComponent(runId)}/stream-token`);
  if (!url) return null;
  try {
    const res = await fetch(url, { method: "POST", headers: { Accept: "application/json", ...(await authHeaders()) } });
    if (!res.ok) return null;
    const body = (await res.json()) as { streamToken?: unknown };
    const token = typeof body.streamToken === "string" && body.streamToken ? body.streamToken : null;
    if (token) storage()?.setItem(STREAM_KEY_PREFIX + runId, token);
    return token;
  } catch {
    return null;
  }
}

/**
 * Opens the SSE stream at /runs/{id}/events?st=<streamToken>. EventSource cannot send headers, so the stream token from
 * POST /runs is kept in sessionStorage (a reload of /studio?run=id can rejoin). A tab with no stored token (an account's
 * other device, or a new tab) asks the server for one first; a finished run still replays without it. The server sends
 * `id: <seq>`, so the browser resumes with Last-Event-ID on reconnect. Returns a function that closes the stream.
 */
export function openEventStream(runId: string, h: StreamHandlers): () => void {
  const base = liveUrl(`/runs/${encodeURIComponent(runId)}/events`);
  if (!base) {
    h.onError(true);
    return () => {};
  }
  let source: EventSource | null = null;
  let stopped = false;
  const connect = (streamToken: string | null) => {
    if (stopped) return;
    const es = new EventSource(streamToken ? `${base}?st=${encodeURIComponent(streamToken)}` : base);
    source = es;
    es.onopen = () => h.onOpen();
    es.onmessage = (msg: MessageEvent<string>) => {
      let json: unknown;
      try {
        json = JSON.parse(msg.data);
      } catch {
        if (process.env.NODE_ENV !== "production") console.warn("[verity] live event was not JSON", msg.data);
        return;
      }
      const parsed = RunEventSchema.safeParse(json);
      if (!parsed.success) {
        if (process.env.NODE_ENV !== "production") console.warn("[verity] live event rejected by schema", parsed.error.issues);
        return;
      }
      h.onEvent(parsed.data as RunEvent);
    };
    es.onerror = () => h.onError(es.readyState === EventSource.CLOSED);
  };
  const stored = storage()?.getItem(STREAM_KEY_PREFIX + runId);
  if (stored) connect(stored);
  else void fetchStreamToken(runId).then(connect);
  return () => {
    stopped = true;
    source?.close();
  };
}
