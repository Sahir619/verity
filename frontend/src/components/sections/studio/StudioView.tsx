"use client";

import { useSearchParams } from "next/navigation";
import { useLiveRun } from "@/lib/hooks/use-live-run";
import { useReplayDriver } from "@/lib/hooks/use-replay-driver";
import { StudioWorkspace } from "./StudioWorkspace";

/** Recordings under public/runs/. Any other `?run=` id is a live run. */
const REPLAY_RUNS = ["golden", "mac4-real"];

function StudioReplay({ name }: { name: string }) {
  useReplayDriver({ src: `/runs/${name}.json`, autoplay: true, loop: false });
  return <StudioWorkspace mode="replay" />;
}

function StudioLive({ runId }: { runId: string }) {
  useLiveRun(runId);
  return <StudioWorkspace mode="live" />;
}

/** Client root of /studio. `?run=mac4-real` (the default) or another recording replays; any other id is a live run. */
export function StudioView() {
  const runId = useSearchParams().get("run") ?? "mac4-real";
  return REPLAY_RUNS.includes(runId) ? <StudioReplay key={runId} name={runId} /> : <StudioLive key={runId} runId={runId} />;
}
