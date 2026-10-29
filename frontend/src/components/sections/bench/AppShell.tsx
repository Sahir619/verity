"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { Menu, PanelLeftOpen } from "lucide-react";
import { useMediaQuery } from "@/lib/hooks/use-media-query";
import { setSidebarShut, useSidebarShut } from "@/lib/hooks/use-sidebar";
import { STUDIO_ROOT } from "../studio/studio-root";

/**
 * The bench frame, after the Claude desktop layout: a sidebar, a centred chat column, and a right panel that exists
 * only while a run is in view and the panel is open. From 900px the sidebar closes to give the chat its width, and the
 * same button that opens the drawer on a phone brings it back. Below 900px the sidebar is a drawer and the panel a sheet.
 * Keeps the studio scope (.vs) for its fonts, status squares and the versions table.
 */
export function AppShell({
  sidebar,
  panel,
  drawerOpen,
  onDrawer,
  drawerFirstRef,
  title,
  empty,
  label = "Chat",
  actions,
  children,
}: {
  sidebar: ReactNode;
  panel: ReactNode;
  drawerOpen: boolean;
  onDrawer(open: boolean): void;
  /** The first control in the drawer, focused when it opens. */
  drawerFirstRef: RefObject<HTMLButtonElement | null>;
  /** The chip of the run in view, shown above the chat. */
  title: string | null;
  /** Nothing in the thread yet: the welcome, the chat box and the examples sit in the middle. */
  empty: boolean;
  /** What the main area is called for a screen reader: the chat, or a page that shares this frame. */
  label?: string;
  /** Buttons at the right end of the top bar (the bench's Open the panel). */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const menuRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const shut = useSidebarShut();
  const wide = useMediaQuery("(min-width: 900px)");

  // Focus follows the drawer: in to its first item on open, back to the menu button on close.
  useEffect(() => {
    if (drawerOpen) drawerFirstRef.current?.focus();
    else if (wasOpen.current) menuRef.current?.focus();
    wasOpen.current = drawerOpen;
  }, [drawerOpen, drawerFirstRef]);

  return (
    <div className={`${STUDIO_ROOT} bench`} data-panel={panel ? "open" : undefined} data-side={shut ? "shut" : undefined}>
      {sidebar}
      {drawerOpen ? <div className="b-scrim" aria-hidden onClick={() => onDrawer(false)} /> : null}
      {/* inert while the drawer is open: focus stays in the drawer and the chat behind the scrim cannot be reached. */}
      <main className="b-main" aria-label={label} data-empty={empty ? "" : undefined} inert={drawerOpen}>
        <header className="b-top" data-title={title ? "" : undefined}>
          <button
            ref={menuRef}
            type="button"
            id="bench-menu"
            className="b-ib b-menu"
            aria-label="Open the sidebar"
            aria-expanded={drawerOpen}
            aria-controls="bench-side"
            onClick={() => {
              if (!wide) return onDrawer(true);
              setSidebarShut(false);
              requestAnimationFrame(() => drawerFirstRef.current?.focus());
            }}
          >
            <Menu aria-hidden className="b-ico-n" />
            <PanelLeftOpen aria-hidden className="b-ico-w" />
          </button>
          {title ? <p className="b-top-t">{title}</p> : null}
          {actions}
        </header>
        {children}
      </main>
      {panel}
    </div>
  );
}
