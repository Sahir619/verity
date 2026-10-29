import { existsSync } from "node:fs";
import path from "node:path";

// Server only (node:fs): import it from server components only (the `server-only` package is not installed, and no dependency
// is added for it). The page is a static export, so whether a loop exists is decided once, at build time.
// process.cwd() is frontend/ during next build and next dev, so "public" resolves to frontend/public.
// The loops are recorded by hand and dropped into public/landing/; until one lands, its slot shows the poster as a still.
export function hasLanding(file: string): boolean {
  return existsSync(path.join(process.cwd(), "public", "landing", file));
}
