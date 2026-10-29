"use client";

import { useSyncExternalStore } from "react";
import { sessionToken, subscribeSession } from "@/lib/sources/live";

/** True when a live session token is stored in sessionStorage (after the access code was accepted). */
export function useLiveSession(): boolean {
  return useSyncExternalStore(subscribeSession, sessionToken, () => null) !== null;
}
