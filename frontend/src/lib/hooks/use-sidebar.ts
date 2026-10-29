"use client";

import { useSyncExternalStore } from "react";

// Whether the reader closed the sidebar (900px and up), kept so the bench and the workspace page open the same way.
const KEY = "verity.sidebar";
const EVENT = "verity:sidebar";

// Read from the store once, then kept here, so a blocked store still closes it for this page.
let shut: boolean | null = null;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function read(): boolean {
  if (shut === null) shut = storage()?.getItem(KEY) === "shut";
  return shut;
}

function subscribe(onChange: () => void): () => void {
  // Closed or opened in another tab: read the store again.
  const onStorage = () => {
    shut = null;
    onChange();
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** True while the sidebar is closed. Open on the server and before the browser store is read. */
export function useSidebarShut(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}

/** Closes or opens the sidebar, keeps the choice, and tells subscribers. */
export function setSidebarShut(next: boolean): void {
  shut = next;
  try {
    if (next) storage()?.setItem(KEY, "shut");
    else storage()?.removeItem(KEY);
  } catch {
    // blocked: the next load opens it again
  }
  window.dispatchEvent(new Event(EVENT));
}
