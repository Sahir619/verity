import type { StageId } from "./types";

/** Ordered pipeline stages. Labels and hints are the exact copy. */
export const STAGES: { id: StageId; label: string; hint: string }[] = [
  { id: "spec", label: "Spec", hint: "Your description, read by Nemotron." },
  { id: "plan", label: "Plan", hint: "Blocks drafted by the agent." },
  { id: "lock", label: "Lock", hint: "Golden model and tests locked." },
  { id: "rtl", label: "RTL", hint: "Verilog written." },
  { id: "lint", label: "Lint", hint: "Verilator checks the code." },
  { id: "sim", label: "Sim", hint: "iverilog and cocotb run the locked tests." },
  { id: "fix", label: "Fix", hint: "The agent repairs failing versions." },
  { id: "synth", label: "Synth", hint: "Yosys and OpenSTA estimate area, timing, power after synthesis (no wires)." },
  { id: "evolve", label: "Evolve", hint: "New versions compete in small rounds." },
  { id: "prove", label: "Prove", hint: "Yosys EQY checks the winner equals v1." },
];
