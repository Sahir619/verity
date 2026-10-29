"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";
function subscribeReduced(onChange: () => void) {
  const mq = window.matchMedia(REDUCED);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
const reducedNow = () => window.matchMedia(REDUCED).matches;
const reducedOnServer = () => false;

type Props = {
  src: string;
  poster: string;
  /** What the clip shows. It is the still's alt text and names the pause button. */
  label: string;
  /** Decided at build time (media.ts): without the MP4 the poster is shown as a still with no controls. */
  hasVideo: boolean;
  width: number;
  height: number;
};

/**
 * A short muted loop. It plays only while on screen and stops off screen. A visible Pause button lets anyone stop it
 * (WCAG 2.2.2). With reduced motion it starts paused on the poster. The button names the video's real state (from its
 * play and pause events), so it reads Pause only while the clip is moving.
 */
export function LoopVideo({ src, poster, label, hasVideo, width, height }: Props) {
  const ref = useRef<HTMLVideoElement>(null);
  const reduced = useSyncExternalStore(subscribeReduced, reducedNow, reducedOnServer);
  const [choice, setChoice] = useState<"play" | "pause" | null>(null);
  const paused = choice === null ? reduced : choice === "pause";
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (paused) {
      v.pause();
      return;
    }
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) v.play().catch(() => {});
        else v.pause();
      },
      { threshold: 0.25 },
    );
    io.observe(v);
    return () => io.disconnect();
  }, [paused]);

  // Playing now means pause for good. Not playing (paused, off screen or autoplay refused) means play now, and stay playing.
  const onToggle = () => {
    if (playing) {
      setChoice("pause");
      return;
    }
    setChoice("play");
    ref.current?.play().catch(() => {});
  };

  if (!hasVideo) {
    return (
      <div className="lv">
        {/* eslint-disable-next-line @next/next/no-img-element -- static export: a plain img with its real size */}
        <img src={poster} alt={label} width={width} height={height} loading="lazy" decoding="async" />
      </div>
    );
  }

  return (
    <div className="lv">
      <video
        ref={ref}
        src={src}
        poster={poster}
        width={width}
        height={height}
        muted
        loop
        playsInline
        preload="none"
        aria-label={label}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
      />
      <button type="button" className="lv-btn" onClick={onToggle} aria-label={`${playing ? "Pause" : "Play"}: ${label}`}>
        {playing ? "Pause" : "Play"}
      </button>
    </div>
  );
}
