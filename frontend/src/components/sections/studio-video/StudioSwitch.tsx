"use client";

import type { ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { StudioView } from "../studio/StudioView";

/** /studio has two faces. No `run` in the address: the run video (the server-rendered `video`, which is also the static
 *  HTML and the Suspense fallback). Any `?run=<id>`: the replay or live studio, exactly as before. */
export function StudioSwitch({ video }: { video: ReactNode }) {
  const run = useSearchParams().get("run");
  return run ? <StudioView /> : video;
}
