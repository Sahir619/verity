import JSZip from "jszip";
import type { Candidate, RunEvent, RunLog, RunState } from "@/lib/contracts/types";
import { useVerityStore } from "@/lib/store";

const HONESTY_NOTE = "Relative to our own v1, open-source tools (Yosys and OpenSTA on Nangate45, post-synthesis estimate, no wires). Not signoff.";
const SAMPLE_NOTICE = "SAMPLE DATA: placeholder files for UI development.";

/** `verity-<runId>.zip`. The run id is reduced to safe file name characters. */
export function zipFileName(run: Pick<RunState, "runId">): string {
  const safe = run.runId.replace(/[^A-Za-z0-9._-]+/g, "-") || "run";
  return `verity-${safe}.zip`;
}

function rtlFile(c: Candidate): string {
  return `rtl/${c.label}.v`;
}

function readmeText(run: RunState, files: string[]): string {
  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  const baseline = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const lines: string[] = [];
  lines.push("Verity design export");
  lines.push("");
  if (run.sample) {
    lines.push(SAMPLE_NOTICE);
    lines.push("");
  }
  lines.push(`Run: ${run.title} (${run.runId}, ${run.mode})`);
  lines.push(winner ? `Winner: ${winner.label}${run.winnerBy === "user" ? " (picked by the user)" : " (picked by fitness)"}` : "Winner: none");
  if (baseline) lines.push(`Baseline: ${baseline.label}`);
  lines.push("");
  lines.push("About these numbers");
  lines.push(HONESTY_NOTE);
  lines.push("Every score comes from tool reports, never from a model's opinion. The golden model and tests were locked away from the agent.");
  lines.push("");
  lines.push("Contents");
  for (const f of files) lines.push(`  ${f}`);
  lines.push("");
  return lines.join("\n");
}

/**
 * The run log as a valid RunLog. Events come from the store (the recording or the live stream).
 * A local winner override is appended as a human + winner pair so the exported log matches what the user saw.
 */
function runLogJson(run: RunState): string {
  const st = useVerityStore.getState();
  const events: RunEvent[] = st.log.slice();
  const last = events.length > 0 ? events[events.length - 1] : undefined;
  let seq = last ? last.seq : run.lastSeq;
  const tMs = last ? last.tMs : run.tMs;
  if (run.winnerBy === "user" && run.winnerId && !events.some((e) => e.t === "winner" && e.by === "user")) {
    events.push({ seq: ++seq, tMs, t: "human", input: { kind: "winner_override", tMs, candidateId: run.winnerId, local: true } });
    events.push({ seq: ++seq, tMs, t: "winner", candidateId: run.winnerId, by: "user", aiPickId: run.aiWinnerId ?? run.winnerId });
  }
  const meta: RunLog["meta"] = st.logMeta ?? {
    runId: run.runId,
    sample: run.sample,
    title: run.title,
    presetId: run.spec?.presetId ?? "",
    recordedAt: new Date().toISOString(),
    durationMs: tMs,
    chapters: [],
  };
  const log: RunLog = { format: "verity.runlog/1", meta, events };
  return JSON.stringify(log, null, 2);
}

/**
 * Builds the design zip: rtl/ (winner and baseline, or every version when there is no winner),
 * tests/ and reports/ from the run's artifacts, run-log.json and README.txt.
 */
export async function buildRunZip(run: RunState): Promise<Blob> {
  const zip = new JSZip();
  const rtl = zip.folder("rtl");
  const tests = zip.folder("tests");
  const reports = zip.folder("reports");
  if (!rtl || !tests || !reports) throw new Error("Could not create the zip folders.");

  const files: string[] = [];
  const add = (path: string, content: string) => {
    zip.file(path, content);
    files.push(path);
  };

  const winner = run.winnerId ? run.candidates[run.winnerId] : undefined;
  const baseline = run.baselineId ? run.candidates[run.baselineId] : undefined;
  const rtlCandidates: Candidate[] = [];
  if (winner) rtlCandidates.push(winner);
  if (baseline && baseline.id !== winner?.id) rtlCandidates.push(baseline);
  if (!winner) {
    // No winner: keep every attempt so the failures can be studied.
    for (const id of run.candidateOrder) if (run.candidates[id] && !rtlCandidates.includes(run.candidates[id])) rtlCandidates.push(run.candidates[id]);
  }
  for (const c of rtlCandidates) add(rtlFile(c), c.rtl);

  for (const a of run.artifacts) {
    if (a.path.startsWith("tests/") || a.path.startsWith("reports/")) add(a.path, a.content);
  }

  add("run-log.json", runLogJson(run));
  // README last so its contents list is complete.
  const readme = readmeText(run, [...files, "README.txt"]);
  zip.file("README.txt", readme);

  return zip.generateAsync({ type: "blob", compression: "DEFLATE" });
}

/** Downloads a blob through an object URL and an anchor click. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a moment to start the download before releasing the URL.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
