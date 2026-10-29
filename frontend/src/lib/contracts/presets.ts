import type { Preset } from "./view-types";

/**
 * Live-run presets. mac4 is the recorded chip (ARCHITECTURE 11.1).
 * The matmul4 and alu8 specs are plausible sample data, not final copy from the agent team.
 */
export const PRESETS: Preset[] = [
  {
    id: "mac4",
    title: "4-lane MAC array, 8-bit",
    oneLine: "Same chip as the recorded run.",
    approxMinutes: 6,
    recommended: true,
    spec: {
      presetId: "mac4",
      title: "4-lane MAC array, 8-bit",
      text:
        "A 4-lane multiply-accumulate unit. Each cycle it multiplies four pairs of unsigned 8-bit inputs and adds the four products to a running sum. After 16 cycles it raises done and holds the 22-bit result until reset. Smallest area matters most.",
      ports: [
        { name: "clk", dir: "in", width: 1 },
        { name: "rst_n", dir: "in", width: 1 },
        { name: "start", dir: "in", width: 1 },
        { name: "a", dir: "in", width: 8, count: 4 },
        { name: "b", dir: "in", width: 8, count: 4 },
        { name: "acc", dir: "out", width: 22 },
        { name: "done", dir: "out", width: 1 },
      ],
      priority: "area",
      testCount: 16,
      maxLines: 300,
    },
  },
  {
    id: "matmul4",
    title: "4x4 matrix multiply, 8-bit",
    oneLine: "Sixteen results from two small matrices.",
    approxMinutes: 8,
    spec: {
      presetId: "matmul4",
      title: "4x4 matrix multiply, 8-bit",
      text:
        "A 4x4 matrix multiplier. It reads two matrices of unsigned 8-bit values, one row of each per cycle, and computes the 16 dot products of the result matrix. After 24 cycles it raises done and holds the 20-bit results until reset. Smallest area matters most.",
      ports: [
        { name: "clk", dir: "in", width: 1 },
        { name: "rst_n", dir: "in", width: 1 },
        { name: "start", dir: "in", width: 1 },
        { name: "a_row", dir: "in", width: 8, count: 4 },
        { name: "b_row", dir: "in", width: 8, count: 4 },
        { name: "c", dir: "out", width: 20, count: 16 },
        { name: "done", dir: "out", width: 1 },
      ],
      priority: "area",
      testCount: 20,
      maxLines: 300,
    },
  },
  {
    id: "alu8",
    title: "8-bit ALU",
    oneLine: "Smallest design, quickest run.",
    approxMinutes: 4,
    spec: {
      presetId: "alu8",
      title: "8-bit ALU",
      text:
        "An 8-bit arithmetic logic unit. A 3-bit opcode selects add, subtract, and, or, xor, shift left, shift right or compare. It sets a zero flag and a carry flag. The result is combinational and registered on the clock edge. Smallest area matters most.",
      ports: [
        { name: "clk", dir: "in", width: 1 },
        { name: "rst_n", dir: "in", width: 1 },
        { name: "a", dir: "in", width: 8 },
        { name: "b", dir: "in", width: 8 },
        { name: "op", dir: "in", width: 3 },
        { name: "y", dir: "out", width: 8 },
        { name: "zero", dir: "out", width: 1 },
        { name: "carry", dir: "out", width: 1 },
      ],
      priority: "area",
      testCount: 24,
      maxLines: 200,
    },
  },
];

// ---------- bench envelope (spec 2026-10-17 section 4) ----------
// Ranges are encoded once here and rendered from this table. They are the ranges the real-model range probe passed on
// 2026-10-18 (backend/evals/probe-2026-10-18.md), and they must equal each pack's family.py KNOBS. Set this back to true
// to label the ranges draft on the page while a new range is unproven.

export const RANGES_ARE_DRAFT = false;

export type KnobValue = number | boolean | string;
export type KnobDef =
  | { id: string; label: string; kind: "int"; min: number; max: number; default: number; unit: string }
  | { id: string; label: string; kind: "bool"; default: boolean; on: string; off: string }
  | { id: string; label: string; kind: "bits"; minLen: number; maxLen: number; default: string };

export type Family = {
  /** Backend preset id (the task pack that renders this family). */
  id: string;
  name: string;
  summary: string;
  knobs: KnobDef[];
  /** The backend runs this pack live today at its default knobs, before knob support (health feature "params"). */
  liveAtDefaults: boolean;
  /** Plain title for a set of knob values, e.g. "MAC array, 4 lanes x 8-bit, 16 cycles". */
  describe(v: Record<string, KnobValue>): string;
};

export const FAMILIES: Family[] = [
  {
    id: "mac4",
    name: "MAC array",
    summary: "Lanes of unsigned multiply-accumulate over a fixed number of cycles.",
    knobs: [
      { id: "lanes", label: "Lanes", kind: "int", min: 1, max: 8, default: 4, unit: "lanes" },
      { id: "width", label: "Input width", kind: "int", min: 4, max: 8, default: 8, unit: "bits" },
      { id: "cycles", label: "Accumulate cycles", kind: "int", min: 4, max: 32, default: 16, unit: "cycles" },
    ],
    liveAtDefaults: true,
    describe: (v) => `MAC array, ${v.lanes} lanes x ${v.width}-bit, ${v.cycles} cycles`,
  },
  {
    id: "add8",
    name: "Adder",
    summary: "Combinational adder with carry in and carry out.",
    knobs: [{ id: "width", label: "Width", kind: "int", min: 4, max: 32, default: 8, unit: "bits" }],
    liveAtDefaults: false,
    describe: (v) => `${v.width}-bit adder with carry`,
  },
  {
    id: "cnt8",
    name: "Counter",
    summary: "Up counter that wraps, with a synchronous reset.",
    knobs: [
      { id: "width", label: "Width", kind: "int", min: 4, max: 16, default: 8, unit: "bits" },
      { id: "enable", label: "Enable input", kind: "bool", default: true, on: "With enable", off: "Free-running" },
    ],
    liveAtDefaults: false,
    describe: (v) => `${v.width}-bit counter, ${v.enable ? "with enable" : "free-running"}`,
  },
  {
    id: "fsm_seq",
    name: "Sequence detector",
    summary: "Serial detector for a bit pattern you choose.",
    knobs: [
      { id: "pattern", label: "Pattern", kind: "bits", minLen: 3, maxLen: 8, default: "1011" },
      { id: "overlap", label: "Overlapping matches", kind: "bool", default: true, on: "Overlaps count", off: "No overlaps" },
    ],
    liveAtDefaults: false,
    describe: (v) => `Sequence detector for ${v.pattern}, ${v.overlap ? "overlaps count" : "no overlaps"}`,
  },
];

/** What the bench says no to, shown next to the knobs. */
export const OUT_OF_RANGE: string[] = [
  "GPUs (CPU jobs have their own CPU card)",
  "Caches and memories",
  "Floating point",
  "Analog or mixed-signal",
  "Designs with more than one module",
  "Anything past the knob limits above",
];

export function familyById(id: string): Family | undefined {
  return FAMILIES.find((f) => f.id === id);
}

export function knobDefaults(f: Family): Record<string, KnobValue> {
  return Object.fromEntries(f.knobs.map((k) => [k.id, k.default]));
}

/** Why a value is outside its knob's range, or null when it is in range. */
export function knobError(k: KnobDef, value: KnobValue | undefined): string | null {
  switch (k.kind) {
    case "int":
      return typeof value === "number" && Number.isInteger(value) && value >= k.min && value <= k.max
        ? null
        : `${k.label} must be a whole number from ${k.min} to ${k.max}.`;
    case "bool":
      return typeof value === "boolean" ? null : `${k.label} must be on or off.`;
    case "bits":
      return typeof value === "string" && value.length >= k.minLen && value.length <= k.maxLen && /^[01]+$/.test(value)
        ? null
        : `${k.label} must be ${k.minLen} to ${k.maxLen} bits of 0 and 1.`;
  }
}

/** Keeps only the family's own knobs, each in range; anything else falls back to its default. */
export function sanitizeKnobs(f: Family, raw: Record<string, unknown>): Record<string, KnobValue> {
  return Object.fromEntries(
    f.knobs.map((k) => {
      const v = raw[k.id] as KnobValue | undefined;
      return [k.id, knobError(k, v) === null ? (v as KnobValue) : k.default];
    }),
  );
}

export function isDefaultKnobs(f: Family, v: Record<string, KnobValue>): boolean {
  return f.knobs.every((k) => v[k.id] === k.default);
}
