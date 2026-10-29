import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { z } from "zod";

// Server only (node:fs): import it from server components only. The page is a static export, so whether the run video
// exists is decided once, at build time. process.cwd() is frontend/ during next build and next dev.
// public/studio/ is written by scripts/make-video.mjs from a real capture; nothing in it is typed by hand.

const ManifestSchema = z.object({
  recordedAt: z.string().min(1),
  provider: z.literal("nebius"),
  jobIds: z.array(z.string().min(1)),
  realSeconds: z.number().positive(),
  videoSeconds: z.number().positive(),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  chapters: z.array(z.object({ t: z.number().nonnegative(), label: z.string().min(1) })).min(1),
});
export type RunManifest = z.infer<typeof ManifestSchema>;

export type RunVideo = {
  manifest: RunManifest;
  /** Files that exist under public/studio/. The MP4 is required; the rest are used when present. */
  poster: string;
  webm: boolean;
  captions: boolean;
};

const STUDIO_DIR = path.join(process.cwd(), "public", "studio");
const has = (file: string) => existsSync(path.join(STUDIO_DIR, file));

/** The recorded run, or null while there is no manifest or no MP4 yet (the page then says it is being recorded). */
export function readRunVideo(): RunVideo | null {
  if (!has("manifest.json") || !has("run.mp4")) return null;
  try {
    const raw: unknown = JSON.parse(readFileSync(path.join(STUDIO_DIR, "manifest.json"), "utf8"));
    const parsed = ManifestSchema.safeParse(raw);
    if (!parsed.success) return null;
    return {
      manifest: parsed.data,
      poster: has("run-poster.webp") ? "/studio/run-poster.webp" : "/landing/hero-bench.webp",
      webm: has("run.webm"),
      captions: has("captions.vtt"),
    };
  } catch {
    return null;
  }
}
