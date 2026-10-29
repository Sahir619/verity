#!/usr/bin/env node
// Turns a capture (scripts/capture-run.mjs) into the studio run video and its data. A dev tool: ffmpeg comes from the
// ffmpeg-static devDependency and nothing here ships to the site.
//
//   node scripts/make-video.mjs --in <capture dir> [--target 180] [--out public/studio] [--loops] [--loops-dir <dir>]
//
// Writes into --out: run.mp4, run.webm, run-poster.webp, captions.vtt, manifest.json.
// --loops also cuts loop-ask.mp4, loop-build.mp4 and loop-upgrade.mp4 (6 s, muted) into --loops-dir
// (default: the landing folder next to --out, so public/landing when --out is public/studio).
//
// Chapter labels are fixed phrases in this file, placed at times taken from log.jsonl (frame index / fps). Nothing is typed.

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import ffmpegPath from "ffmpeg-static";
import sharp from "sharp";

const WIDTH = 1440;
const HEIGHT = 900;
const MIN_FPS = 4;
const MAX_FPS = 30;
const MIN_GAP_S = 2;
const LOOP_S = 6;

// ---------- args ----------
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    let [k, v] = a.slice(2).split(/=(.*)/s, 2);
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
if (!args.in || !args.out) {
  console.log("usage: node scripts/make-video.mjs --in <capture dir> --out <dir> [--target 180] [--loops] [--loops-dir <dir>]");
  process.exit(1);
}
const IN = path.resolve(args.in);
const OUT = path.resolve(args.out);
const TARGET = Number(args.target ?? 180);
const LOOPS = args.loops === "true";
const LOOPS_DIR = path.resolve(args["loops-dir"] ?? path.join(OUT, "..", "landing"));
const fail = (msg) => {
  console.error(msg);
  process.exit(1);
};
if (!(TARGET > 0)) fail("--target must be a number of seconds above 0");
if (!ffmpegPath || !existsSync(ffmpegPath)) fail("ffmpeg-static has no binary here. Run npm install in frontend/.");

// ---------- inputs ----------
const framesDir = path.join(IN, "frames");
const frames = existsSync(framesDir) ? readdirSync(framesDir).filter((f) => /^\d+\.png$/.test(f)).sort() : [];
if (!frames.length) fail(`No frames in ${framesDir}`);
frames.forEach((f, i) => {
  if (Number(f.replace(".png", "")) !== i + 1) fail(`Frames must be numbered 000001.png upward with no gaps (found ${f} at position ${i + 1}).`);
});
const logPath = path.join(IN, "log.jsonl");
if (!existsSync(logPath)) fail(`No ${logPath}`);
const log = readFileSync(logPath, "utf8")
  .split(/\r?\n/)
  .filter((l) => l.trim())
  .map((l, i) => {
    try {
      return JSON.parse(l);
    } catch {
      return fail(`log.jsonl line ${i + 1} is not JSON`);
    }
  });
const firstJob = log.find((e) => e.kind === "job_created");
if (!firstJob) fail("log.jsonl has no job_created line, so there is no run to show.");

const fps = Math.min(MAX_FPS, Math.max(MIN_FPS, frames.length / TARGET));
const videoSeconds = frames.length / fps;
const timeOf = (frame) => Math.min(Math.max(0, (frame - 1) / fps), Math.max(0, videoSeconds - 1 / fps)); // frame is 1-based, 0 = none yet
console.log(`${frames.length} frames at ${fps.toFixed(2)} fps = ${videoSeconds.toFixed(1)} s (target ${TARGET} s)`);

// ---------- chapters (fixed labels, times from the log) ----------
const primary = firstJob.jobId;
const upgrade = log.find((e) => e.kind === "upgrade_created");
const onPrimary = (e) => e.jobId === primary;
const candidates = [];
const add = (label, ev, must = false) => {
  if (ev) candidates.push({ label, t: timeOf(ev.frame ?? 0), must, seq: log.indexOf(ev) });
};
candidates.push({ label: "You ask for a CPU", t: 0, must: true, seq: -1 });
add("The blocks start", log.find((e) => onPrimary(e) && e.kind === "block_status" && e.status === "running"));
add("A block repairs its code", log.find((e) => onPrimary(e) && e.kind === "block_status" && (e.repairs ?? 0) > 0));
add("The core joins the blocks", log.find((e) => onPrimary(e) && ((e.kind === "core_status" && e.status === "running") || (e.kind === "job_status" && e.status === "integrating"))));
// The core passed its own run while the job is still working: the programs and riscv-formal run after that.
add("Programs and riscv-formal", log.find((e) => onPrimary(e) && e.kind === "core_status" && e.status === "passed"));
add("The CPU passes", log.find((e) => onPrimary(e) && e.kind === "job_status" && e.status === "passed"), true);
add("The CPU failed", log.find((e) => onPrimary(e) && e.kind === "job_status" && e.status === "failed"), true);
if (upgrade) {
  add("You upgrade it", upgrade, true);
  add("The upgrade passes", log.find((e) => e.jobId === upgrade.jobId && e.kind === "job_status" && e.status === "passed"), true);
  add("The upgrade failed", log.find((e) => e.jobId === upgrade.jobId && e.kind === "job_status" && e.status === "failed"), true);
}
candidates.sort((a, b) => a.t - b.t || a.seq - b.seq);

const chapters = [];
const seen = new Set();
for (const c of candidates) {
  if (seen.has(c.label)) continue;
  const prev = chapters[chapters.length - 1];
  if (prev && c.t - prev.t < MIN_GAP_S) {
    // Too close: a result chapter beats a passing step before it (never the opening one).
    if (c.must && !prev.must) {
      seen.delete(prev.label);
      chapters.pop();
      const before = chapters[chapters.length - 1];
      if (before && c.t - before.t < MIN_GAP_S) continue;
    } else continue;
  }
  seen.add(c.label);
  chapters.push(c);
}
console.log("chapters:");
for (const c of chapters) console.log(`  ${c.t.toFixed(1).padStart(7)} s  ${c.label}`);

// ---------- encode ----------
mkdirSync(OUT, { recursive: true });
const vf = `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p`;
function ffmpeg(list, label) {
  const r = spawnSync(ffmpegPath, ["-hide_banner", "-loglevel", "error", "-y", ...list], { encoding: "utf8" });
  if (r.status !== 0) fail(`ffmpeg failed (${label}):\n${r.stderr || r.error}`);
}
const input = ["-framerate", String(fps), "-start_number", "1", "-i", path.join(framesDir, "%06d.png")];
const mp4 = path.join(OUT, "run.mp4");
const webm = path.join(OUT, "run.webm");
console.log("encoding run.mp4");
ffmpeg([...input, "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-g", String(Math.round(fps * 2)), "-an", "-movflags", "+faststart", mp4], "mp4");
console.log("encoding run.webm");
ffmpeg([...input, "-vf", vf, "-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0", "-row-mt", "1", "-g", String(Math.round(fps * 2)), "-an", webm], "webm");
console.log("writing run-poster.webp");
// The poster is the frame where the first job passes (the whole board is green there); the last frame if it never did.
const passed = log.find((e) => e.jobId === primary && e.kind === "job_status" && e.status === "passed");
const posterFrame = passed ? frames[Math.min(frames.length, Math.max(1, passed.frame)) - 1] : frames[frames.length - 1];
await sharp(path.join(framesDir, posterFrame))
  .resize(WIDTH, HEIGHT, { fit: "contain", background: "#000000" })
  .webp({ quality: 82 })
  .toFile(path.join(OUT, "run-poster.webp"));

// ---------- captions ----------
const stamp = (s) => {
  const ms = Math.round(s * 1000);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(Math.floor(ms / 3600000))}:${p(Math.floor((ms % 3600000) / 60000))}:${p(Math.floor((ms % 60000) / 1000))}.${p(ms % 1000, 3)}`;
};
const cues = chapters.map((c, i) => {
  const end = i + 1 < chapters.length ? chapters[i + 1].t : videoSeconds;
  return `${i + 1}\n${stamp(c.t)} --> ${stamp(Math.max(end, c.t + 0.5))}\n${c.label}\n`;
});
writeFileSync(path.join(OUT, "captions.vtt"), `WEBVTT\n\n${cues.join("\n")}`);

// ---------- manifest ----------
const jobIds = [];
for (const e of log) if (e.jobId && !jobIds.includes(e.jobId)) jobIds.push(e.jobId);
const terminals = log.filter((e) => e.kind === "job_status" && ["passed", "failed", "stopped"].includes(e.status));
const endMs = terminals.length ? Math.max(...terminals.map((e) => e.realMs)) : Math.max(...log.map((e) => e.realMs));
let recordedAt = firstJob.createdAt;
if (!recordedAt || Number.isNaN(Date.parse(recordedAt))) {
  recordedAt = statSync(logPath).mtime.toISOString();
  console.warn("log.jsonl has no createdAt on its first job; recordedAt falls back to the log file's date.");
}
const manifest = {
  recordedAt,
  provider: "nebius",
  jobIds,
  realSeconds: Math.max(1, Math.round((endMs - firstJob.realMs) / 1000)),
  videoSeconds: Math.round(videoSeconds * 10) / 10,
  width: WIDTH,
  height: HEIGHT,
  chapters: chapters.map((c) => ({ t: Math.round(c.t * 10) / 10, label: c.label })),
};
writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");

// ---------- loops for the landing ----------
if (LOOPS) {
  mkdirSync(LOOPS_DIR, { recursive: true });
  // Each loop shows the part of the bench a small slot can still read, at 16:10 in the 1440x900 frame:
  // the chat for the request, the CPU board panel for the build and the upgrade.
  const CROP_CHAT = "crop=720:450:270:0";
  const CROP_PANEL = "crop=432:270:1008:52";
  // The first chapter that exists, so a short run still gets all three loops.
  const at = (...labels) => labels.map((l) => chapters.find((c) => c.label === l)?.t).find((t) => t !== undefined);
  const loops = [
    ["loop-ask.mp4", 0, CROP_CHAT],
    ["loop-build.mp4", at("The blocks start", "A block repairs its code", "The core joins the blocks"), CROP_PANEL],
    ["loop-upgrade.mp4", at("You upgrade it", "The upgrade passes", "The upgrade failed"), CROP_PANEL],
  ];
  for (const [name, at0, crop] of loops) {
    // A chapter near the end still gets a full loop: start early enough to fit LOOP_S.
    const start = at0 === undefined ? undefined : Math.max(0, Math.min(at0, videoSeconds - LOOP_S));
    if (start === undefined) {
      console.log(`skipping ${name}: its chapter is not in this run`);
      continue;
    }
    console.log(`cutting ${name} from ${start.toFixed(1)} s`);
    ffmpeg(["-ss", String(start), "-i", mp4, "-t", String(LOOP_S), "-vf", `${crop},scale=960:600:flags=lanczos`, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", path.join(LOOPS_DIR, name)], name);
  }
}

console.log(`done: ${OUT}`);
console.log(`  run.mp4 ${(statSync(mp4).size / 1e6).toFixed(1)} MB, run.webm ${(statSync(webm).size / 1e6).toFixed(1)} MB, ${chapters.length} chapters, real ${manifest.realSeconds} s, video ${manifest.videoSeconds} s`);
