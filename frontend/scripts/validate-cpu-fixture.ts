// Validates the real CPU run file: the path given as the first argument, else public/runs/cpu-real.json.
// `npm run validate:fixture` runs it after the run-log checks (see package.json); for one file:
//   node scripts/validate-cpu-fixture.ts path/to/cpu-real.json
// Relies on Node 24 type stripping: erasable TypeScript only, no imports from src.
import { readFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type Json = Record<string, unknown>;

const problems: string[] = [];
const fail = (msg: string) => {
  problems.push(msg);
};

const arg = process.argv[2];
const path = arg ? resolve(arg) : fileURLToPath(new URL("../public/runs/cpu-real.json", import.meta.url));
const name = basename(path);
const text = readFileSync(path, "utf8");
const root = JSON.parse(text) as Json;

const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isCount = (v: unknown): v is number => isNum(v) && Number.isInteger(v) && v >= 0;
const obj = (v: unknown, what: string): Json => {
  if (isObj(v)) return v;
  fail(`${what} is missing or not an object`);
  return {};
};
const arr = (v: unknown, what: string): unknown[] => {
  if (Array.isArray(v)) return v;
  fail(`${what} is missing or not an array`);
  return [];
};
/** passed <= total, both counts. */
const ratio = (p: unknown, t: unknown, what: string) => {
  if (!isCount(p) || !isCount(t)) {
    fail(`${what}: passed and total must be counts (got ${String(p)} and ${String(t)})`);
    return;
  }
  if (p > t) fail(`${what}: ${p} is more than ${t}`);
};

if (typeof root.note !== "string" || !/export_cpu_fixture\.py/.test(root.note) || !/hand-edit/i.test(root.note)) {
  fail("note must say the file is exported by export_cpu_fixture.py and must not be hand-edited");
}
if (/[\u2013\u2014]/.test(text)) fail("the file contains an en or em dash");
if (typeof root.recordedAt !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(root.recordedAt)) fail("recordedAt must be YYYY-MM-DD");

const EXPECTED_BLOCKS = ["rv32i_imm", "rv32i_dec", "rv32i_alu", "rv32i_br", "rv32i_lsu", "rv32i_rf", "rv32i_ctl"];

function checkJob(j: Json, label: string) {
  if (typeof j.jobId !== "string" || !/^cpu_[0-9a-f]{32}$/.test(j.jobId)) fail(`${label}: jobId is not a cpu job id`);
  if (j.status !== "passed") fail(`${label}: status is ${String(j.status)}, not passed`);
  if (!isNum(j.spentUsd) || j.spentUsd < 0) fail(`${label}: spentUsd must be a non-negative number`);
  for (const k of ["createdAt", "finishedAt"]) {
    if (typeof j[k] !== "string" || Number.isNaN(Date.parse(j[k] as string))) fail(`${label}: ${k} is not a time`);
  }
  if (typeof j.createdAt === "string" && typeof j.finishedAt === "string" && Date.parse(j.finishedAt) < Date.parse(j.createdAt)) {
    fail(`${label}: finishedAt is before createdAt`);
  }
  const core = obj(j.core, `${label}.core`);
  if (core.status !== "passed") fail(`${label}: core status is ${String(core.status)}`);
  const ev = obj(core.evidence, `${label}.core.evidence`);
  ratio(ev.testsPassed, ev.testsTotal, `${label} core programs`);
  if (isCount(ev.testsPassed) && isCount(ev.testsTotal) && ev.testsPassed !== ev.testsTotal) {
    fail(`${label}: core programs ${ev.testsPassed}/${ev.testsTotal} did not all pass`);
  }
  const v1 = arr(j.versions, `${label}.versions`)
    .map((v) => obj(v, `${label}.versions[]`))
    .find((v) => v.version === 1);
  if (!v1) {
    fail(`${label}: no CPU v1 in versions`);
    return;
  }
  ratio(v1.rvfiPassed, v1.rvfiTotal, `${label} riscv-formal`);
  if (!isCount(v1.rvfiTimeouts)) fail(`${label}: rvfiTimeouts must be a count`);
  if (!Array.isArray(v1.rvfiFailed)) fail(`${label}: rvfiFailed must be a list`);
  else if (isCount(v1.rvfiPassed) && isCount(v1.rvfiTotal) && isCount(v1.rvfiTimeouts)) {
    if (v1.rvfiPassed + v1.rvfiTimeouts + v1.rvfiFailed.length !== v1.rvfiTotal) {
      fail(`${label}: riscv-formal passed + timeouts + failed does not equal the total`);
    }
  }
  const profile = obj(v1.profile, `${label} CPU v1 profile`);
  for (const k of ["cpi", "clockNs", "benchTimeUs"]) {
    if (!isNum(profile[k]) || (profile[k] as number) <= 0) fail(`${label}: profile.${k} must be a positive number`);
  }
  const totals = obj(v1.totals, `${label} CPU v1 totals`);
  if (!isNum(totals.areaUm2) || totals.areaUm2 <= 0) fail(`${label}: CPU v1 area must be a positive number`);
  for (const b of arr(j.blocks, `${label}.blocks`).map((x) => obj(x, `${label}.blocks[]`))) {
    const id = String(b.blockId);
    if (b.status !== "passed") fail(`${label}: block ${id} is ${String(b.status)}`);
    const e = obj(b.evidence, `${label} block ${id} evidence`);
    ratio(e.testsPassed, e.testsTotal, `${label} block ${id} tests`);
    ratio(e.mutantsKilled, e.mutantsTotal, `${label} block ${id} planted bugs`);
    if (!isCount(e.repairs)) fail(`${label}: block ${id} repairs must be a count`);
    if (!isNum(b.usd) || b.usd < 0) fail(`${label}: block ${id} spend must be a non-negative number`);
  }
}

const parent = obj(root.parent, "parent");
checkJob(parent, "parent");
if (parent.parentJobId !== null) fail("parent: parentJobId must be null");
const pBlocks = arr(parent.blocks, "parent.blocks").map((b) => obj(b, "parent.blocks[]"));
if (pBlocks.length !== 7) fail(`parent: expected 7 blocks, found ${pBlocks.length}`);
for (const id of EXPECTED_BLOCKS) if (!pBlocks.some((b) => b.blockId === id)) fail(`parent: block ${id} is missing`);
if (pBlocks.some((b) => b.reused === true)) fail("parent: no block may be reused");
for (const b of pBlocks) {
  if (b.usedVersion === "v1" && obj(b.evidence, "evidence").eqyVsV1 === "proven") {
    fail(`parent: block ${String(b.blockId)} uses v1 although its proof passed`);
  }
}

const ups = arr(root.upgrades, "upgrades").map((u) => obj(u, "upgrades[]"));
for (const kind of ["pipeline_2", "cache_16"]) {
  const u = ups.find((x) => x.upgradeId === kind);
  if (!u) {
    fail(`upgrades: ${kind} is missing`);
    continue;
  }
  checkJob(u, kind);
  if (u.parentJobId !== parent.jobId) fail(`${kind}: parentJobId does not reference the parent job`);
  const cmp = obj(u.comparison, `${kind}.comparison`);
  for (const k of ["parentAreaUm2", "areaUm2", "parentClockNs", "clockNs"]) {
    if (!isNum(cmp[k]) || (cmp[k] as number) <= 0) fail(`${kind}: comparison.${k} must be a positive number`);
  }
  for (const k of ["areaDeltaPct", "clockDeltaPct"]) if (!isNum(cmp[k])) fail(`${kind}: comparison.${k} must be a number`);
  const fast = obj(cmp.fast, `${kind}.comparison.fast`);
  if (!isNum(fast.benchTimeRatio) || fast.benchTimeRatio <= 0) fail(`${kind}: fast benchTimeRatio must be a positive number`);
  if (!isNum(fast.cpiDeltaPct)) fail(`${kind}: fast cpiDeltaPct must be a number`);
  const blocks = arr(u.blocks, `${kind}.blocks`).map((b) => obj(b, `${kind}.blocks[]`));
  const reused = blocks.filter((b) => b.reused === true);
  if (reused.length < 6) fail(`${kind}: expected at least 6 blocks reused from the parent`);
  if (blocks.length - reused.length !== 1) fail(`${kind}: expected exactly one new block`);
  // A reused block carries the parent's own run id.
  for (const b of reused) {
    const same = pBlocks.find((p) => p.blockId === b.blockId);
    if (!same || same.runId !== b.runId) fail(`${kind}: reused block ${String(b.blockId)} does not match the parent's run`);
  }
}
if (ups.length !== 2) fail(`upgrades: expected 2 jobs, found ${ups.length}`);

if (problems.length > 0) {
  console.error(`${name} validation failed:`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
const spend = [parent, ...ups].reduce((s, j) => s + (j.spentUsd as number), 0);
console.log(`${name} OK: parent ${String(parent.jobId)}, ${pBlocks.length} blocks, ${ups.length} upgrades, total spend $${spend.toFixed(4)}`);
