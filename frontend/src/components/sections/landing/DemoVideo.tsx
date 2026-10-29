"use client";

import { useState } from "react";
import { DEMO_VIDEO_URL } from "./links";

/** The YouTube video id in a youtu.be, youtube.com/watch?v=, /embed/ or /shorts/ link, else null. */
function youTubeId(url: string): string | null {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  const host = u.hostname.replace(/^www\.|^m\./, "");
  const ok = (id: string | null | undefined) => (id && /^[\w-]{6,}$/.test(id) ? id : null);
  if (host === "youtu.be") return ok(u.pathname.split("/")[1]);
  if (host === "youtube.com" || host === "youtube-nocookie.com") {
    if (u.pathname === "/watch") return ok(u.searchParams.get("v"));
    const m = u.pathname.match(/^\/(?:embed|shorts)\/([^/]+)/);
    return ok(m?.[1]);
  }
  return null;
}

/**
 * The full demo. A poster with a play button; the YouTube player (no-cookie domain) loads only after a click, so the page
 * makes no third-party call first. Renders nothing while NEXT_PUBLIC_DEMO_VIDEO_URL is unset.
 */
export function DemoVideo() {
  const [on, setOn] = useState(false);
  if (!DEMO_VIDEO_URL) return null;
  const id = youTubeId(DEMO_VIDEO_URL);
  return (
    <section className="sec" id="demo" aria-labelledby="demo-h">
      <h2 id="demo-h">The whole story in under 3 minutes</h2>
      <div className="demo-f">
        {id === null ? (
          <a className="btn s" href={DEMO_VIDEO_URL} target="_blank" rel="noreferrer">
            Watch the demo
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ) : on ? (
          <iframe
            src={`https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`}
            title="Verity demo video"
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
          />
        ) : (
          <button type="button" className="demo-p" onClick={() => setOn(true)} aria-label="Play the demo video">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export: a plain img with its real size */}
            <img src="/landing/hero-bench.webp" alt="" width={1440} height={900} loading="lazy" decoding="async" />
            <span className="btn s demo-b">Play the demo</span>
          </button>
        )}
      </div>
    </section>
  );
}
