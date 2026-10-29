"use client";

import { useRef, useState } from "react";

export type PlayerChapter = { t: number; label: string };

type Props = {
  poster: string;
  width: number;
  height: number;
  webm: boolean;
  captions: boolean;
  chapters: PlayerChapter[];
};

/** m:ss from seconds. */
function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** The index of the chapter the playhead is in: the last one that has started. */
function activeIndex(chapters: PlayerChapter[], time: number): number {
  let at = 0;
  chapters.forEach((c, i) => {
    if (c.t <= time + 0.05) at = i;
  });
  return at;
}

/**
 * The run video and its chapter list. Nothing autoplays (so reduced motion is respected by construction); the native
 * controls pause and play it. A chapter button seeks and plays, and the active one reads "Now" beside a filled square,
 * so the state is a shape and a word, not colour alone.
 */
export function RunPlayer({ poster, width, height, webm, captions, chapters }: Props) {
  const ref = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const active = activeIndex(chapters, time);

  const seek = (t: number) => {
    const v = ref.current;
    if (!v) return;
    v.currentTime = t;
    setTime(t);
    v.play().catch(() => {});
  };

  return (
    <>
      <div className="sv-frame">
        <video
          ref={ref}
          poster={poster}
          width={width}
          height={height}
          controls
          preload="metadata"
          playsInline
          aria-label="Verity building an RV32I CPU, one real run, sped up"
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          onSeeked={(e) => setTime(e.currentTarget.currentTime)}
        >
          <source src="/studio/run.mp4" type="video/mp4" />
          {webm ? <source src="/studio/run.webm" type="video/webm" /> : null}
          {captions ? <track kind="captions" srcLang="en" label="English" src="/studio/captions.vtt" default /> : null}
        </video>
      </div>
      <ol className="sv-chapters" aria-label="Chapters">
        {chapters.map((c, i) => (
          <li key={`${c.t}-${c.label}`}>
            <button type="button" className="sv-chap" aria-current={i === active ? "true" : undefined} onClick={() => seek(c.t)}>
              <span className="sv-time">{clock(c.t)}</span>
              <span className="sv-label">{c.label}</span>
              <span className="sv-now">
                {i === active ? (
                  <>
                    <span className="sv-sq" aria-hidden="true" />
                    Now
                  </>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </>
  );
}
