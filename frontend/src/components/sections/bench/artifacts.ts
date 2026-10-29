// The run's artifact files the bench reads, each checked before it is drawn: sign-off sheets, waveforms, the pack's rules,
// circuits and the synthesis and timing reports. An artifact can arrive cut short (the server caps event size), so one that
// does not parse reads as "could not be read", never as a crash or a made-up value. Every field here comes from run data.

import { z } from "zod";
import type { Candidate, RunState } from "@/lib/contracts/types";

/** A file the run sent, by its path, or null. The reducer keeps one entry per path (the newest). */
export function artifactText(run: RunState, path: string): string | null {
  return run.artifacts.find((a) => a.path === path)?.content ?? null;
}

/** "missing": the run sent no such file. "bad": it sent one that does not read (cut short, or another format). */
export type Read<T> = { state: "ok"; value: T } | { state: "missing" } | { state: "bad" };

function readJson<T>(run: RunState, path: string, schema: z.ZodType<T>): Read<T> {
  const text = artifactText(run, path);
  if (text === null) return { state: "missing" };
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    return { state: "bad" };
  }
  const parsed = schema.safeParse(json);
  return parsed.success ? { state: "ok", value: parsed.data } : { state: "bad" };
}

const count = z.number().int().nonnegative();

// ---------- sign-off sheet: signoff/<candidateId>.json ----------

export const SignoffSchema = z.object({
  candidateId: z.string(),
  tests: z.object({
    seedsRun: count,
    seedsPassed: count,
    perSeed: z.array(z.object({ seed: z.number(), passed: count, total: count })).default([]),
  }),
  lint: z.object({
    errors: count,
    warnings: count,
    byTag: z.record(z.string(), count).default({}),
    latches: count,
  }),
  eqyVsV1: z.enum(["proven", "inconclusive", "not_equivalent", "not_run", "v1_is_winner"]),
  eqyVsHuman: z.enum(["proven", "inconclusive", "not_equivalent", "not_run"]),
  strength: z
    .object({
      planted: count,
      caught: count,
      harmless: count,
      undecided: count,
      status: z.enum(["ok", "skipped", "failed"]),
      note: z.string().optional(),
    })
    .nullable(),
  durationMs: z.number().nonnegative().default(0),
});
export type Signoff = z.infer<typeof SignoffSchema>;

export function signoffOf(run: RunState, candidateId: string): Read<Signoff> {
  return readJson(run, `signoff/${candidateId}.json`, SignoffSchema);
}

// ---------- waveform: waves/<candidateId>.json ----------

// One value per cycle. A number is shown as it came; anything else that is not text reads as unknown ("x").
const value = z.union([z.string(), z.number()]).transform(String).catch("x");

export const WavesSchema = z.object({
  candidateId: z.string(),
  case: z.string(),
  failCycle: z.number().int().nullable(),
  startCycle: z.number().int().default(0),
  signals: z
    .array(
      z.object({
        name: z.string(),
        width: z.number().int().positive(),
        got: z.array(value),
        expected: z.array(value).optional(),
      }),
    )
    .min(1),
  note: z.string().optional(),
});
export type Waves = z.infer<typeof WavesSchema>;

export function wavesOf(run: RunState, candidateId: string): Read<Waves> {
  return readJson(run, `waves/${candidateId}.json`, WavesSchema);
}

// ---------- the pack's rules: spec/rules.json ----------

export function specRulesOf(run: RunState): Read<string[]> {
  const r = readJson(run, "spec/rules.json", z.object({ rules: z.array(z.string()) }));
  return r.state === "ok" ? { state: "ok", value: r.value.rules.filter((x) => x.trim() !== "") } : r;
}

// ---------- circuit: circuit/<candidateId>.json (verity.circuit/1, backend/verity/contracts/circuit.py) ----------

export const CIRCUIT_KINDS = ["port", "reg", "add", "mul", "mux", "cmp", "logic", "submodule"] as const;
export type CircuitKind = (typeof CIRCUIT_KINDS)[number];

export const CircuitSchema = z.object({
  format: z.literal("verity.circuit/1"),
  candidateId: z.string().default(""),
  top: z.string(),
  ports: z.array(z.object({ name: z.string(), dir: z.enum(["in", "out"]), width: z.number(), signed: z.boolean().default(false) })).default([]),
  nodes: z.array(z.object({ id: z.string(), kind: z.enum(CIRCUIT_KINDS), label: z.string(), width: z.number(), module: z.string().default("") })),
  edges: z.array(z.object({ source: z.string(), target: z.string(), width: z.number() })).default([]),
  truncated: z.boolean().default(false),
  stats: z.object({ regs: count, adders: count, multipliers: count, muxes: count }).partial().default({}),
});
export type Circuit = z.infer<typeof CircuitSchema>;

/** The circuit of one version. Edges whose ends are not drawn are left out (the server checks this too). */
export function circuitOf(run: RunState, candidateId: string): Read<Circuit> {
  const r = readJson(run, `circuit/${candidateId}.json`, CircuitSchema);
  if (r.state !== "ok") return r;
  const ids = new Set(r.value.nodes.map((n) => n.id));
  return { state: "ok", value: { ...r.value, edges: r.value.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) } };
}

// ---------- synthesis and timing reports: reports/synth_<label>.txt, reports/sta_<label>.txt ----------

export type SynthReport = { cells: number | null; top: { cell: string; count: number }[]; text: string };

/** The cell count and the most used cell types from the synthesis report, as the report states them. */
export function synthReportOf(run: RunState, c: Candidate): SynthReport | null {
  const text = artifactText(run, `reports/synth_${c.label}.txt`);
  if (text === null) return null;
  const head = text.split("--- log")[0];
  const cells = /^Cells:\s*(\d+)/m.exec(head);
  const top: SynthReport["top"] = [];
  for (const m of head.matchAll(/^\s{2}([A-Za-z0-9_]+):\s*(\d+)\s*$/gm)) top.push({ cell: m[1], count: Number(m[2]) });
  return { cells: cells ? Number(cells[1]) : null, top: top.slice(0, 5), text };
}

export type TimingReport = { minPeriodNs: number | null; start: string | null; end: string | null; arrivalNs: number | null; text: string };

/** The slowest path from the timing report: where it starts and ends, and when the data arrives, as the report states them. */
export function timingReportOf(run: RunState, c: Candidate): TimingReport | null {
  const text = artifactText(run, `reports/sta_${c.label}.txt`);
  if (text === null) return null;
  const num = (re: RegExp) => {
    const m = re.exec(text);
    return m ? Number(m[1]) : null;
  };
  const start = /^Startpoint:\s*(.+)$/m.exec(text)?.[1]?.trim() ?? null;
  const end = /^Endpoint:\s*(.+)$/m.exec(text)?.[1]?.trim() ?? null;
  return {
    minPeriodNs: num(/Minimum clock period:\s*([\d.]+)\s*ns/),
    start,
    end,
    arrivalNs: num(/([\d.]+)\s+data arrival time/),
    text,
  };
}
