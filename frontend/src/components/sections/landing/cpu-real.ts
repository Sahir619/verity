// Every CPU figure on the landing comes from the real jobs (public/runs/cpu-real.json), read at build time.
// The file is exported by backend/evals/export_cpu_fixture.py and is never hand-edited. Nothing here is typed by hand:
// run the script again and the landing follows. validate-cpu-fixture.ts checks the file itself.
// Only what the landing renders is read: the block list, the parent core's program and riscv-formal counts, the pipeline
// benchmark time ratio on fast memory, and the spend over all three jobs.
import raw from "../../../../public/runs/cpu-real.json";
import { formatUsd } from "@/lib/format";

type Pair = [number, number];

type FixBlock = { title: string; status: string; usedVersion: string | null };
type FixVersion = { version: number; rvfiPassed: number | null; rvfiTotal: number | null };
type FixJob = {
  jobId: string;
  upgradeId: string | null;
  parentJobId: string | null;
  status: string;
  spentUsd: number;
  blocks: FixBlock[];
  core: { status: string; evidence: { testsPassed: number | null; testsTotal: number | null } };
  versions: FixVersion[];
  comparison: { fast: { benchTimeRatio: number | null } | null } | null;
};
type Fixture = { recordedAt: string; provider: string; parent: FixJob; upgrades: FixJob[] };

const fix = raw as unknown as Fixture;

function need<T>(value: T | null | undefined, what: string): T {
  if (value === null || value === undefined) throw new Error(`cpu-real.json is missing ${what}`);
  return value;
}
const pair = (p: number | null, t: number | null, what: string): Pair => [need(p, `${what} passed`), need(t, `${what} total`)];

/** An upgrade job that passed and upgrades the parent. */
function upgrade(upgradeId: string, parentId: string) {
  const job = need(fix.upgrades.find((u) => u.upgradeId === upgradeId), `the ${upgradeId} job`);
  if (job.parentJobId !== parentId) throw new Error(`cpu-real.json: ${upgradeId} does not upgrade the parent`);
  if (job.status !== "passed") throw new Error(`cpu-real.json: ${upgradeId} did not pass`);
  return job;
}

const parent = fix.parent;
if (parent.status !== "passed" || parent.core.status !== "passed") throw new Error("cpu-real.json: the parent job did not pass");
if (parent.parentJobId !== null) throw new Error("cpu-real.json: the parent job is an upgrade job");

const blocks = parent.blocks.map((b) => ({
  title: b.title,
  passed: b.status === "passed",
  // The CPU takes v1 of this block because the model's winner was not proven equal to v1.
  usesV1NotProven: b.usedVersion === "v1",
}));
const v1 = need(parent.versions.find((x) => x.version === 1), `${parent.jobId} CPU v1`);

const pipelineJob = upgrade("pipeline_2", parent.jobId);
const cacheJob = upgrade("cache_16", parent.jobId);
const ratio = need(need(need(pipelineJob.comparison, "pipeline_2 comparison").fast, "pipeline_2 fast-memory comparison").benchTimeRatio, "pipeline_2 benchmark time ratio");

const perJobSpendUsd = [parent.spentUsd, pipelineJob.spentUsd, cacheJob.spentUsd];
if (!perJobSpendUsd.every((usd) => usd >= 0)) throw new Error("cpu-real.json: a job has no spend");
const totalSpendUsd = perJobSpendUsd.reduce((s, usd) => s + usd, 0);

export const CPU = {
  date: fix.recordedAt,
  provider: fix.provider,
  blocks,
  blocksPassed: blocks.filter((b) => b.passed).length,
  blocksTotal: blocks.length,
  // CPU v1, the first integrated core, as the board shows it.
  core: {
    programs: pair(parent.core.evidence.testsPassed, parent.core.evidence.testsTotal, "core programs"),
    riscvFormal: pair(v1.rvfiPassed, v1.rvfiTotal, "core riscv-formal"),
  },
  pipeline: { benchTimeRatioText: `${ratio.toFixed(2)}x` },
  perJobSpendUsd,
  totalSpendText: formatUsd(totalSpendUsd),
} as const;
