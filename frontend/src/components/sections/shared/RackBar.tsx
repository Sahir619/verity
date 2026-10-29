"use client";

import { useEffect, useRef, type ReactNode } from "react";
import Link from "next/link";
import { Logo } from "./Logo";
import "./rack-bar.css";

export type RackPage = "home" | "studio" | "bench" | "results" | "other";

export type RackBarProps = {
  /** The page this bar sits on, so its link is marked current. */
  current?: RackPage;
  /** Optional run context (title, badges), shown after the links. */
  context?: ReactNode;
  /** Right-hand slot: the page's badge and its one action. */
  actions?: ReactNode;
  /** The page links. Defaults to the four shared links; the landing passes its own in-page links. */
  links?: RackBarNavLink[];
  /** Opt-in (the landing): the bar is pinned to the top, hides on scroll down and comes back on any scroll up. */
  revealOnScrollUp?: boolean;
};

/** One bar link. `external` opens in a new tab (for example GitHub). */
export type RackBarNavLink = { href: string; label: string; page?: RackPage; external?: boolean };

const LINKS: RackBarNavLink[] = [
  { href: "/#how", label: "How it works" },
  { href: "/#inside", label: "Why trust it" },
  { href: "/studio", label: "Studio", page: "studio" },
  { href: "/bench", label: "Live bench", page: "bench" },
];

/** Scroll distance (px) that counts as a direction change, so a few px of jitter never toggles the bar. */
const REVEAL_STEP = 6;
/** Scroll position (px) under which the bar is "at the top" and sits as the page draws it. */
const REVEAL_TOP = 8;

/** Writes the bar's reveal state (data-rv): "top" (as drawn), "show" (pinned over content) or "hide" (slid up).
 *  A passive scroll listener with rAF throttling and no React state, like VignetteGrid. */
function useRevealOnScrollUp(ref: { current: HTMLElement | null }, on: boolean) {
  useEffect(() => {
    const el = ref.current;
    if (!on || !el) return;
    let anchor = window.scrollY;
    let raf = 0;
    const set = (v: string) => {
      if (el.dataset.rv !== v) el.dataset.rv = v;
    };
    const frame = () => {
      raf = 0;
      const y = Math.max(0, window.scrollY);
      if (y <= REVEAL_TOP) {
        anchor = y;
        set("top");
        return;
      }
      const dy = y - anchor;
      if (Math.abs(dy) < REVEAL_STEP) return;
      anchor = y;
      if (dy < 0) set("show");
      else if (y > el.offsetHeight) set("hide");
      else set("show");
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(frame);
    };
    frame();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [ref, on]);
}

/** The rack top bar shared by every page: brand, the page links, a context slot and an actions slot. */
export function RackBar({ current = "other", context, actions, links = LINKS, revealOnScrollUp = false }: RackBarProps) {
  const ref = useRef<HTMLElement>(null);
  useRevealOnScrollUp(ref, revealOnScrollUp);
  return (
    <header ref={ref} className="rkbar" data-rv={revealOnScrollUp ? "top" : undefined}>
      <Link prefetch={false} href="/" className="rkbar-brand" aria-label="Verity home">
        <Logo />
      </Link>
      <nav aria-label="Primary" className="rkbar-nav">
        <ul>
          {links.map((l) => (
            <li key={l.href}>
              {l.external ? (
                <a href={l.href} target="_blank" rel="noreferrer">
                  {l.label}
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              ) : (
                <Link prefetch={false} href={l.href} aria-current={l.page === current ? "page" : undefined}>
                  {l.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      </nav>
      {context ? <div className="rkbar-ctx">{context}</div> : null}
      {actions ? <div className="rkbar-r">{actions}</div> : null}
    </header>
  );
}

/** A bar action in the rack button style: "p" is the page's one primary action, "s" a secondary one. */
export function RackBarLink({ href, kind = "s", children }: { href: string; kind?: "p" | "s"; children: ReactNode }) {
  return (
    <Link prefetch={false} href={href} className={`rkbar-btn ${kind}`}>
      {children}
      {kind === "p" ? (
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2 8h11M9 4l4 4-4 4" />
        </svg>
      ) : null}
    </Link>
  );
}
