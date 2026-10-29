import { Suspense } from "react";
import type { Metadata } from "next";
import { StudioSwitch } from "@/components/sections/studio-video/StudioSwitch";
import { StudioVideo } from "@/components/sections/studio-video/StudioVideo";

export const metadata: Metadata = { title: "Studio: watch Verity build a CPU" };

// No `run` in the address: the recorded CPU run (the static HTML). `?run=<id>`: the replay or live studio, as before.
export default function StudioPage() {
  const video = <StudioVideo />;
  return (
    <Suspense fallback={video}>
      <StudioSwitch video={video} />
    </Suspense>
  );
}
