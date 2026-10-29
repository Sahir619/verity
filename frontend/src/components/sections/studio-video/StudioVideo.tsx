import { LIVE_HREF } from "@/lib/env";
import { RackBar, RackBarLink } from "../shared/RackBar";
import { RACK_FACES } from "../shared/rack-fonts";
import { RunPlayer } from "./RunPlayer";
import { readRunVideo, type RunManifest } from "./manifest";
import "../rack/rack.css";
import "./studio-video.css";

/** "4 minutes" or "50 seconds", from a real number of seconds. */
function span(seconds: number): string {
  if (seconds < 90) {
    const s = Math.max(1, Math.round(seconds));
    return `${s} ${s === 1 ? "second" : "seconds"}`;
  }
  return `${Math.round(seconds / 60)} minutes`;
}

/** "October 28, 2026" from the manifest's recordedAt, read in UTC so the build machine's zone cannot move the day. */
function day(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

function Recorded({ m, poster, webm, captions }: { m: RunManifest; poster: string; webm: boolean; captions: boolean }) {
  return (
    <>
      <p className="lead">
        One real run on Nebius, from the request to a passed RV32I core. {span(m.realSeconds)}, shown in {span(m.videoSeconds)}.
      </p>
      <div className="sv-player">
        <RunPlayer poster={poster} width={m.width} height={m.height} webm={webm} captions={captions} chapters={m.chapters} />
      </div>
      <p className="small sv-cap">Real CPU jobs, Nebius, {day(m.recordedAt)}. Sped up; every frame is the real bench. Nothing is staged.</p>
      <p className="sv-ids" aria-label="Job ids">
        {m.jobIds.map((id) => (
          <span key={id}>{id}</span>
        ))}
      </p>
    </>
  );
}

/** /studio with no run in the address: the recorded CPU run as a plain video with chapters. Everything shown comes from
 *  public/studio/manifest.json, written by scripts/make-video.mjs. Until it and run.mp4 exist, only the poster and one line. */
export function StudioVideo() {
  const video = readRunVideo();
  return (
    <div className={`rk land sv-page ${RACK_FACES}`}>
      <RackBar
        current="studio"
        actions={
          <RackBarLink href={LIVE_HREF} kind="p">
            Try it
          </RackBarLink>
        }
      />
      <main className="sv">
        <h1>Watch Verity build a CPU</h1>
        {video ? (
          <Recorded m={video.manifest} poster={video.poster} webm={video.webm} captions={video.captions} />
        ) : (
          <>
            <p className="lead">The run video is being recorded.</p>
            <div className="sv-frame sv-still">
              {/* eslint-disable-next-line @next/next/no-img-element -- static export: a plain img with its real size */}
              <img src="/landing/hero-bench.webp" alt="The Verity bench" width={1440} height={900} decoding="async" />
            </div>
          </>
        )}
      </main>
    </div>
  );
}
