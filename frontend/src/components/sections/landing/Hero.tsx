import Link from "next/link";
import { LIVE_HREF } from "@/lib/env";
import { ArrowIcon } from "../rack/icons";
import { DEMO_VIDEO_URL } from "./links";

// The real size of public/landing/hero-bench.webp, so the image reserves its space and the page does not jump.
const HERO_W = 1440;
const HERO_H = 900;

/** The first screen: the one line, what decides a pass, the one button, and the Verity bench under it. */
export function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-h">
      <h1 id="hero-h">
        <span>Describe a chip.</span> <span>Get Verilog that is tested, measured and checked.</span>
      </h1>
      <p className="lead">
        NVIDIA Nemotron writes every line. Locked tests decide what passes, and a formal check throws out any smaller version that differs from v1.
      </p>
      <div className="hero-acts">
        <Link prefetch={false} href={LIVE_HREF} className="btn p">
          Try it
          <ArrowIcon />
        </Link>
        {DEMO_VIDEO_URL ? (
          <a href="#demo" className="tlink">
            Watch the demo
          </a>
        ) : (
          <Link prefetch={false} href="/studio/" className="tlink">
            Watch the run
          </Link>
        )}
      </div>
      <p className="small hero-cred">Built on NVIDIA Nemotron and Nebius Token Factory</p>
      <figure className="hero-shot">
        {/* eslint-disable-next-line @next/next/no-img-element -- static export: a plain img with its real size */}
        <img
          src="/landing/hero-bench.webp"
          width={HERO_W}
          height={HERO_H}
          loading="eager"
          fetchPriority="high"
          decoding="async"
          alt="The Verity bench: a chat with a RISC-V CPU request on the left and the CPU board with every block marked Passed on the right"
        />
      </figure>
    </section>
  );
}
