"use client";

import { useCallback, useState } from "react";
import { buildRunZip, downloadBlob } from "@/lib/export/zip";
import { useRun } from "@/lib/store";

export type ZipState = "idle" | "busy" | "error";

/** Builds and downloads the run zip. File name: verity-<runId>.zip. */
export function useRunZip(): { state: ZipState; download: () => Promise<void> } {
  const run = useRun((r) => r);
  const [state, setState] = useState<ZipState>("idle");
  const download = useCallback(async () => {
    setState("busy");
    try {
      const blob = await buildRunZip(run);
      downloadBlob(blob, `verity-${run.runId}.zip`);
      setState("idle");
    } catch {
      setState("error");
    }
  }, [run]);
  return { state, download };
}
