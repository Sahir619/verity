#!/usr/bin/env node
// Films a live CPU run. It only WATCHES: it never navigates, clicks or POSTs. You drive the bench by hand in the default
// agent-browser session; this script takes screenshots of that tab and reads the CPU jobs with GET.
//
//   node scripts/capture-run.mjs --out ./capture-1 [--api http://127.0.0.1:8000] [--every 2000] [--poll 15000]
//        [--stop-file <path>] [--follow-children true|false] [--diff 1.5] [--keepalive 30000] [--idle-stop 60]
//        [--token <bearer>]   (or VERITY_TOKEN in the environment; only needed for a signed-in account, an anonymous or
//                              local-admin session is matched by this machine's address)
//
// Output: <out>/frames/000001.png ... and <out>/log.jsonl, one line per job state change:
//   { realMs, frame, jobId, kind, blockId?, title?, status?, repairs? }
// kind: job_created | block_status | core_status | job_status | upgrade_created. realMs is milliseconds since this script
// started; frame is the index of the last kept frame when the change was seen (the frame taken just after it).
// Make the video from it with scripts/make-video.mjs.

import { exec, execFile } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import path from "node:path";
import sharp from "sharp";

// ---------- args ----------
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    let [k, v] = a.slice(2).split(/=(.*)/s, 2);
    if (k.startsWith("no-")) {
      out[k.slice(3)] = "false";
      continue;
    }
    if (v === undefined) {
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("--")) {
        v = next;
        i++;
      } else v = "true";
    }
    out[k] = v;
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
if (!args.out || args.help) {
  console.log("usage: node scripts/capture-run.mjs --out <dir> [--api URL] [--every ms] [--poll ms] [--stop-file path] [--follow-children true|false] [--diff 1.5] [--keepalive ms] [--idle-stop s] [--token t]");
  process.exit(args.out ? 0 : 1);
}
const OUT = path.resolve(args.out);
const API = (args.api || "http://127.0.0.1:8000").replace(/\/+$/, "");
const EVERY = Math.max(250, Number(args.every ?? 2000));
// Job reads are slower than frames: the board polls the same rate bucket (by address), so a fast poll here makes the
// board show "The CPU job request failed" on screen. 15 s keeps the log close enough for chapters.
const POLL = Math.max(5000, Number(args.poll ?? 15000));
let lastPoll = 0;
const STOP_FILE = args["stop-file"] ? path.resolve(args["stop-file"]) : null;
const FOLLOW_CHILDREN = (args["follow-children"] ?? "true") !== "false";
const DIFF_MIN = Number(args.diff ?? 1.5); // mean absolute difference, 0 to 255, on 64x40 greyscale
const KEEPALIVE_MS = Number(args.keepalive ?? 30000); // keep a frame at least this often, even when unchanged
const IDLE_STOP_MS = Number(args["idle-stop"] ?? 60) * 1000;
const TOKEN = args.token || process.env.VERITY_TOKEN || "";

const FRAMES = path.join(OUT, "frames");
const LOG = path.join(OUT, "log.jsonl");
const TMP = path.join(OUT, "tmp-shot.png");
mkdirSync(FRAMES, { recursive: true });
if (readdirSync(FRAMES).some((f) => f.endsWith(".png"))) {
  console.error(`${FRAMES} already has frames. Use a new --out so two captures are not mixed.`);
  process.exit(1);
}

// ---------- agent-browser (the only thing that touches the page, and only to look) ----------
const IS_WIN = process.platform === "win32";
const quote = (s) => (IS_WIN ? `"${String(s).replace(/"/g, '\\"')}"` : String(s));
function ab(cmdArgs) {
  return new Promise((resolve) => {
    const done = (err, stdout, stderr) => resolve({ ok: !err, out: `${stdout || ""}${stderr || ""}`.trim() });
    const opts = { timeout: 30000, windowsHide: true };
    // On Windows the CLI is a .cmd shim, so it goes through one command string (no arguments are passed to a shell).
    if (IS_WIN) exec(["agent-browser", ...cmdArgs.map(quote)].join(" "), opts, done);
    else execFile("agent-browser", cmdArgs, opts, done);
  });
}
// Removes the Next dev badge before each screenshot. Single quotes only, so it survives the shell.
const CLEAN_JS = "document.querySelectorAll('nextjs-portal').forEach(function(e){e.remove()})";

// ---------- frames ----------
const startMs = Date.now();
const realMs = () => Date.now() - startMs;
let kept = 0;
let dropped = 0;
let lastKeptMs = -Infinity;
let lastThumb = null;

async function thumb(file) {
  return sharp(file).resize(64, 40, { fit: "fill" }).greyscale().raw().toBuffer();
}
function meanAbsDiff(a, b) {
  let sum = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) sum += Math.abs(a[i] - b[i]);
  return n ? sum / n : 255;
}

/** One screenshot; keeps it unless it is visually unchanged from the last kept frame (and not overdue). */
async function snap(force) {
  await ab(["eval", CLEAN_JS]);
  const shot = await ab(["screenshot", TMP]);
  if (!shot.ok || !existsSync(TMP)) {
    console.log(`[${secs()}] screenshot failed: ${shot.out.slice(0, 160)}`);
    return;
  }
  let t;
  try {
    t = await thumb(TMP);
  } catch (e) {
    console.log(`[${secs()}] unreadable screenshot: ${e.message}`);
    rmSync(TMP, { force: true });
    return;
  }
  const diff = lastThumb ? meanAbsDiff(lastThumb, t) : Infinity;
  const overdue = realMs() - lastKeptMs >= KEEPALIVE_MS;
  if (force || overdue || diff >= DIFF_MIN) {
    kept++;
    renameSync(TMP, path.join(FRAMES, `${String(kept).padStart(6, "0")}.png`));
    lastThumb = t;
    lastKeptMs = realMs();
  } else {
    dropped++;
    rmSync(TMP, { force: true });
  }
}

// ---------- jobs (GET only) ----------
async function get(pathname) {
  try {
    const res = await fetch(`${API}${pathname}`, {
      headers: { Accept: "application/json", ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return { error: res.status };
    return { json: await res.json() };
  } catch (e) {
    return { error: e.name === "TimeoutError" ? "timeout" : "unreachable" };
  }
}

const TERMINAL = new Set(["passed", "failed", "stopped"]);
let runId = null;
const followed = new Map(); // jobId -> { status, blocks: Map(blockId -> {status, repairs}), core }
let pending = []; // events seen this tick, written after the screenshot
let apiWarned = false;

function note(ev) {
  pending.push(ev);
}
function flushPending() {
  for (const ev of pending) {
    const line = { realMs: realMs(), frame: kept, ...ev };
    appendFileSync(LOG, JSON.stringify(line) + "\n");
    const bits = [ev.kind, ev.jobId, ev.blockId, ev.status, ev.repairs != null ? `repairs ${ev.repairs}` : null].filter(Boolean);
    console.log(`[${secs()}] ${bits.join(" ")}`);
  }
  pending = [];
}

function startFollowing(job, kind) {
  followed.set(job.id, { status: job.status, blocks: new Map(), core: "locked" });
  note({ jobId: job.id, kind, status: job.status, createdAt: job.createdAt });
}

function diffJob(job) {
  const st = followed.get(job.id);
  if (!st) return;
  for (const b of job.blocks ?? []) {
    const prev = st.blocks.get(b.blockId) ?? { status: "waiting", repairs: 0 };
    const repairs = b.evidence?.repairs ?? 0;
    if (b.status !== prev.status || repairs !== prev.repairs) {
      note({ jobId: job.id, kind: "block_status", blockId: b.blockId, title: b.title, status: b.status, repairs });
    }
    st.blocks.set(b.blockId, { status: b.status, repairs });
  }
  const core = job.core?.status ?? "locked";
  if (core !== st.core) {
    note({ jobId: job.id, kind: "core_status", status: core, repairs: job.core?.evidence?.repairs ?? 0 });
    st.core = core;
  }
  if (job.status !== st.status) {
    note({ jobId: job.id, kind: "job_status", status: job.status });
    st.status = job.status;
  }
}

async function pollJobs() {
  const mine = await get("/cpu-jobs/mine");
  if (mine.error !== undefined) {
    if (!apiWarned) console.log(`[${secs()}] GET /cpu-jobs/mine failed (${mine.error}); frames continue`);
    apiWarned = true;
    return;
  }
  apiWarned = false;
  const jobs = Array.isArray(mine.json?.jobs) ? mine.json.jobs : [];

  if (!runId) {
    const fresh = jobs
      .filter((j) => Date.parse(j.createdAt) > startMs && !j.parentJobId)
      .sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt))[0];
    if (fresh) {
      runId = fresh.id;
      startFollowing(fresh, "job_created");
    }
  }
  if (runId && FOLLOW_CHILDREN) {
    for (const j of jobs) {
      if (j.parentJobId === runId && !followed.has(j.id)) startFollowing(j, "upgrade_created");
    }
  }
  for (const id of [...followed.keys()]) {
    const one = await get(`/cpu-jobs/${encodeURIComponent(id)}`);
    const job = one.json ?? jobs.find((j) => j.id === id);
    if (job) diffJob(job);
  }
}

// ---------- loop ----------
function secs() {
  return `${(realMs() / 1000).toFixed(1)}s`;
}
let stopping = null;
let wake = null;
const stop = (why) => {
  if (!stopping) stopping = why;
  if (wake) wake();
};
process.on("SIGINT", () => stop("Ctrl+C"));
process.on("SIGTERM", () => stop("terminated"));
const sleep = (ms) =>
  new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    wake = () => {
      clearTimeout(t);
      resolve();
    };
  });

console.log(`capturing to ${OUT} every ${EVERY} ms (api ${API}, diff ${DIFF_MIN}, keepalive ${KEEPALIVE_MS} ms)`);
console.log("watching only: drive the bench yourself. Stop with Ctrl+C or by creating the stop file.");
let allTerminalSince = null;
while (!stopping) {
  const began = Date.now();
  if (Date.now() - lastPoll >= POLL) {
    lastPoll = Date.now();
    await pollJobs();
  }
  await snap(pending.length > 0);
  flushPending();

  if (STOP_FILE && existsSync(STOP_FILE)) {
    stop("stop file found");
    break;
  }
  const states = [...followed.values()];
  if (states.length && states.every((s) => TERMINAL.has(s.status))) {
    allTerminalSince ??= Date.now();
    if (Date.now() - allTerminalSince >= IDLE_STOP_MS) {
      stop(`every followed job has been finished for ${IDLE_STOP_MS / 1000} s with no new child`);
      break;
    }
  } else allTerminalSince = null;

  if (!stopping) await sleep(Math.max(0, EVERY - (Date.now() - began)));
}

flushPending();
const summary = [...followed.entries()].map(([id, s]) => `${id}=${s.status}`).join(" ") || "no job seen";
console.log(`stopped (${stopping}). frames kept ${kept}, dropped ${dropped}, after ${secs()}. ${summary}`);
console.log(`next: node scripts/make-video.mjs --in ${OUT} --target 180 --out public/studio`);
process.exit(0);
