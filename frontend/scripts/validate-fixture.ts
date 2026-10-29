// Validates one run log: the path given as the first argument, else public/runs/golden.json.
// `npm run validate:fixture` runs it over every fixture in public/runs (see package.json); for one file:
//   node scripts/validate-fixture.ts path/to/runlog.json
// Relies on Node 24 type stripping: erasable TypeScript only, relative import with the .ts extension.
import { existsSync, readFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { RunLogSchema } from "../src/lib/contracts/schemas.ts";

type Ppa = {
  areaUm2: number | null;
  clockPeriodNs: number | null;
  powerMw: number | null;
};
type Cand = {
  id: string;
  status: string;
  rtl: string;
  attempts: { n: number; rtl: string }[];
  ppa: Ppa | null;
  isBaseline: boolean;
  fitness: { score: number; priority: "area" | "speed" | "power"; weights: { area: number; timing: number; power: number } } | null;
};

const WEIGHTS = {
  area: { area: 0.6, timing: 0.25, power: 0.15 },
  speed: { area: 0.25, timing: 0.6, power: 0.15 },
  power: { area: 0.25, timing: 0.15, power: 0.6 },
} as const;

/**
 * ARCHITECTURE 8.3: weighted gain vs the baseline; a missing metric is dropped and the weights renormalized.
 * Returns the score and the effective (renormalized) weights, 0 for a dropped metric (backend/CONTRACTS S8).
 */
function recompute(
  base: Ppa,
  c: Ppa,
  priority: "area" | "speed" | "power",
): { score: number; weights: { area: number; timing: number; power: number } } {
  const w = WEIGHTS[priority];
  const parts: { key: "area" | "timing" | "power"; w: number; gain: number }[] = [];
  const add = (key: "area" | "timing" | "power", b: number | null, v: number | null) => {
    if (b === null || v === null) return;
    parts.push({ key, w: w[key], gain: (100 * (b - v)) / b });
  };
  add("area", base.areaUm2, c.areaUm2);
  add("timing", base.clockPeriodNs, c.clockPeriodNs);
  add("power", base.powerMw, c.powerMw);
  const total = parts.reduce((s, p) => s + p.w, 0);
  const weights = { area: 0, timing: 0, power: 0 };
  if (total === 0) return { score: 0, weights };
  for (const p of parts) weights[p.key] = p.w / total;
  return { score: parts.reduce((s, p) => s + (p.w / total) * p.gain, 0), weights };
}

const problems: string[] = [];
const fail = (msg: string) => problems.push(msg);

const arg = process.argv[2];
const path = arg ? resolve(arg) : fileURLToPath(new URL("../public/runs/golden.json", import.meta.url));
const name = basename(path);
const parsed = RunLogSchema.safeParse(JSON.parse(readFileSync(path, "utf8")));
if (!parsed.success) {
  console.error(`${name} does not match RunLogSchema:`);
  for (const issue of parsed.error.issues) console.error(`  ${issue.path.join(".")}: ${issue.message}`);
  process.exit(1);
}
const log = parsed.data;

// seq strictly increasing, tMs non-decreasing
let prevSeq = 0;
let prevT = 0;
for (const e of log.events) {
  if (e.seq <= prevSeq) fail(`seq not strictly increasing at seq ${e.seq} (previous ${prevSeq})`);
  if (e.tMs < prevT) fail(`tMs decreases at seq ${e.seq} (${e.tMs} < ${prevT})`);
  prevSeq = e.seq;
  prevT = e.tMs;
}
if (prevT > log.meta.durationMs) fail(`last event tMs ${prevT} is after meta.durationMs ${log.meta.durationMs}`);

// canvas ops: connects reference added nodes, content add_node count <= 12
const added = new Set<string>();
let contentNodes = 0;
for (const e of log.events) {
  if (e.t !== "op") continue;
  if (e.op.op === "add_node") {
    if (added.has(e.op.id)) fail(`add_node repeats id ${e.op.id} at seq ${e.seq}`);
    added.add(e.op.id);
    contentNodes += 1;
  } else if (e.op.op === "connect") {
    if (!added.has(e.op.source)) fail(`connect at seq ${e.seq}: source ${e.op.source} was not added before`);
    if (!added.has(e.op.target)) fail(`connect at seq ${e.seq}: target ${e.op.target} was not added before`);
  }
}
// The 12-node visibility budget is enforced by the reducer for recordings; only the hand-built sample must respect it.
if (log.meta.sample && contentNodes > 12) fail(`content add_node count ${contentNodes} exceeds 12`);

// last upsert per candidate
const last = new Map<string, Cand>();
for (const e of log.events) if (e.t === "candidate") last.set(e.candidate.id, e.candidate as Cand);

const baseline = [...last.values()].find((c) => c.isBaseline);
if (!baseline || !baseline.ppa) {
  fail("no measured baseline candidate");
} else {
  for (const c of last.values()) {
    if (c.status === "measured" && !c.fitness) fail(`${c.id} is measured but has no fitness`);
    if (c.status !== "measured" && c.fitness) fail(`${c.id} has fitness but status ${c.status}`);
    if (!c.fitness || !c.ppa) continue;
    const re = recompute(baseline.ppa, c.ppa, c.fitness.priority);
    const expected = c.id === baseline.id ? 0 : re.score;
    if (Math.abs(expected - c.fitness.score) > 0.01) {
      fail(`${c.id} fitness ${c.fitness.score} does not match recomputed ${expected.toFixed(3)}`);
    }
    // Stored weights are the effective weights: the priority weights renormalized over the metrics both versions have
    // (power weight 0 when power is null). Weights are recorded rounded to 4 decimals.
    const sw = c.fitness.weights;
    const ew = re.weights;
    if (Math.abs(sw.area - ew.area) > 0.0005 || Math.abs(sw.timing - ew.timing) > 0.0005 || Math.abs(sw.power - ew.power) > 0.0005) {
      fail(`${c.id} stored weights do not match the effective weights for priority ${c.fitness.priority}`);
    }
  }
}

// winner and sample hygiene
const winner = [...log.events].reverse().find((e) => e.t === "winner");
if (!winner || winner.t !== "winner") fail("no winner event");
else if (!last.has(winner.candidateId)) fail(`winner ${winner.candidateId} is not a known candidate`);
if (log.meta.sample && !log.meta.note) fail("meta.sample is true but meta.note is empty");
for (const c of last.values()) {
  for (const a of c.attempts) {
    const lines = a.rtl.split("\n").length;
    if (log.meta.sample) {
      // Sample RTL is hand-written placeholder text; a recorded run has whatever length the model wrote.
      if (lines < 20 || lines > 40) fail(`${c.id} attempt ${a.n} RTL has ${lines} lines (expected 20 to 40)`);
      if (!a.rtl.includes("// SAMPLE RTL for UI development")) fail(`${c.id} attempt ${a.n} RTL lacks the SAMPLE marker`);
    } else if (a.rtl.includes("SAMPLE RTL")) {
      fail(`${c.id} attempt ${a.n} RTL carries a SAMPLE marker but meta.sample is false`);
    }
  }
}

// A compressed fixture (X.json next to X-raw.json) must equal its raw recording in everything except tMs.
const rawPath = join(dirname(path), `${basename(path, ".json")}-raw.json`);
if (!name.endsWith("-raw.json") && existsSync(rawPath)) {
  const rawLog = RunLogSchema.parse(JSON.parse(readFileSync(rawPath, "utf8")));
  if (rawLog.events.length !== log.events.length) {
    fail(`event count ${log.events.length} differs from the raw recording (${rawLog.events.length})`);
  } else {
    const strip = (e: unknown) => {
      const rest = { ...(e as Record<string, unknown>) };
      delete rest.tMs;
      return JSON.stringify(rest);
    };
    log.events.forEach((e, i) => {
      if (strip(e) !== strip(rawLog.events[i])) fail(`event ${e.seq} differs from the raw recording beyond tMs`);
    });
    if (log.meta.chapters.length !== rawLog.meta.chapters.length) fail("chapter count differs from the raw recording");
    log.meta.chapters.forEach((c, i) => {
      const r = rawLog.meta.chapters[i];
      if (r && (c.id !== r.id || c.label !== r.label)) fail(`chapter ${i} id or label differs from the raw recording`);
    });
    if (log.meta.durationMs > rawLog.meta.durationMs) fail("compressed duration is longer than the raw duration");
    if (!log.meta.note?.includes("idle waits shortened") && !log.meta.note?.includes("Idle waits shortened")) {
      fail("compressed fixture meta.note does not say that idle waits were shortened");
    }
  }
}

if (problems.length > 0) {
  console.error(`${name} validation failed:`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
console.log(
  `${name} OK: ${last.size} versions, winner ${winner && winner.t === "winner" ? winner.candidateId : "none"}, ${contentNodes} nodes, fitness checks passed`,
);
