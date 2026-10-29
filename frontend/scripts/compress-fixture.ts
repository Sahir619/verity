// Derives the playable fixture from the untouched recording by shortening idle gaps only.
//   node scripts/compress-fixture.ts [raw.json] [out.json] [maxGapMs]
// Defaults: public/runs/mac4-real-raw.json -> public/runs/mac4-real.json, maxGapMs 6000 (npm run compress:fixture).
// A gap between two consecutive events longer than maxGapMs is cut to maxGapMs and every later tMs (events and
// chapters) shifts earlier by the same amount. Event order, seq and every payload field and number stay untouched.
// Relies on Node 24 type stripping: erasable TypeScript only.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

type Ev = { seq: number; tMs: number; [k: string]: unknown };
type Log = {
  format: string;
  meta: { durationMs: number; chapters: { id: string; label: string; tMs: number }[]; note?: string; [k: string]: unknown };
  events: Ev[];
};

const dir = (f: string) => fileURLToPath(new URL(`../public/runs/${f}`, import.meta.url));
const inPath = process.argv[2] ? resolve(process.argv[2]) : dir("mac4-real-raw.json");
const outPath = process.argv[3] ? resolve(process.argv[3]) : dir("mac4-real.json");
const maxGap = Number(process.argv[4] ?? 6000);

const clock = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

const raw = JSON.parse(readFileSync(inPath, "utf8")) as Log;

// New time of every distinct old event time.
const mapped = new Map<number, number>();
let shift = 0;
let prevOld = 0;
for (const e of raw.events) {
  const gap = e.tMs - prevOld;
  if (gap > maxGap) shift += gap - maxGap;
  prevOld = e.tMs;
  mapped.set(e.tMs, e.tMs - shift);
}
const remap = (t: number): number => {
  const hit = mapped.get(t);
  if (hit !== undefined) return hit;
  throw new Error(`chapter time ${t} does not coincide with an event time`);
};

const events = raw.events.map((e) => ({ ...e, tMs: remap(e.tMs) }));
const chapters = raw.meta.chapters.map((c) => ({ ...c, tMs: remap(c.tMs) }));
const durationMs = events.reduce((m, e) => Math.max(m, e.tMs), 0);

const note =
  `${raw.meta.note ?? ""} Idle waits shortened: playback of ${clock(durationMs)} from the original ${clock(raw.meta.durationMs)}; ` +
  `every gap over ${maxGap / 1000} s between events is cut to ${maxGap / 1000} s, every event and number unchanged.`;

const out: Log = { ...raw, meta: { ...raw.meta, durationMs, chapters, note: note.trim() }, events };
writeFileSync(outPath, JSON.stringify(out, null, 2) + "\n");
console.log(`${outPath}: ${raw.events.length} events, ${raw.meta.durationMs} ms -> ${durationMs} ms (gap cap ${maxGap} ms)`);
