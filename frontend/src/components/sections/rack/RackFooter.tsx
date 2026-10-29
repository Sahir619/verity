import Link from "next/link";
import { LIVE_HREF } from "@/lib/env";
import { Logo } from "../shared/Logo";
import { DEMO_VIDEO_URL, DEVPOST_URL, GITHUB_URL } from "../landing/links";

const PROJECT_LINKS: { label: string; href: string | null }[] = [
  { label: "GitHub", href: GITHUB_URL },
  { label: "Devpost", href: DEVPOST_URL },
  { label: "Demo video", href: DEMO_VIDEO_URL },
];

const NOTES = [
  "Small blocks and a small RISC-V CPU, not a GPU. Larger chips are not built yet.",
  "Numbers are open-source estimates (Yosys, OpenSTA, Nangate45), always against our own v1.",
  "A proof says the winner matches v1. The locked tests check v1 against the golden model.",
];

/** Footer: the logo, the two ways in, the limits in three short lines, and the credits. Project links render only when their URL is set. */
export function RackFooter() {
  const project = PROJECT_LINKS.filter((l): l is { label: string; href: string } => l.href !== null);
  return (
    <footer className="foot" id="notice">
      <a href="#top" className="foot-logo" aria-label="Verity, back to top">
        <Logo />
      </a>
      <nav aria-label="Footer" className="foot-links">
        <Link prefetch={false} href={LIVE_HREF} className="tlink">
          Try it
        </Link>
        <Link prefetch={false} href="/studio/" className="tlink">
          Watch the run
        </Link>
        {project.map((l) => (
          <a key={l.label} href={l.href} target="_blank" rel="noreferrer" className="tlink">
            {l.label}
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ))}
      </nav>
      <ul className="foot-notes small">
        {NOTES.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      <p className="small foot-legal">
        Nebius x NVIDIA Global AI Hackathon, 2026 · NVIDIA Nemotron on Nebius Token Factory · Evolution ideas from REvolution (Min et
        al., ASP-DAC 2026)
      </p>
    </footer>
  );
}
