import { LIVE_HREF } from "@/lib/env";
import { RackBar, RackBarLink, type RackBarNavLink } from "@/components/sections/shared/RackBar";
import { DEMO_VIDEO_URL, GITHUB_URL } from "../landing/links";

// The landing's own in-page links. Demo and GitHub appear only when their URL is set; other pages keep the shared links.
const HOME_LINKS: RackBarNavLink[] = [
  { href: "#how", label: "How it works" },
  { href: "#cpu", label: "The CPU" },
  ...(DEMO_VIDEO_URL ? [{ href: "#demo", label: "Demo" }] : []),
  ...(GITHUB_URL ? [{ href: GITHUB_URL, label: "GitHub", external: true }] : []),
];

/** Top bar of the landing: the shared rack bar with in-page links, and the live link. The bar's link is
 *  amber like the hero's button, at the user's request (2026-10-28): both say "Try it" and go to the same place. */
export function RackNav() {
  return (
    <RackBar
      current="home"
      revealOnScrollUp
      links={HOME_LINKS}
      actions={
        <>
          <RackBarLink href={LIVE_HREF} kind="p">
            Try it
          </RackBarLink>
        </>
      }
    />
  );
}
