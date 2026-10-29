import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,          // emits /studio/index.html: works on plain static hosts
  images: { unoptimized: true },
  // Dev-only knob: lets parallel builders run isolated dev servers. Production builds use the default.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
