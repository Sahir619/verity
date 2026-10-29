"use client";

import { useEffect, useState } from "react";
import { listMyRuns, listThreads, type MyRun, type ThreadEntry } from "@/lib/sources/live";

/** The chats and runs the server keeps for the signed-in account, so they follow the account to another device. */
export type AccountLists = { threads: ThreadEntry[]; runs: MyRun[]; threadsFailed: boolean; runsFailed: boolean };

const NONE: AccountLists = { threads: [], runs: [], threadsFailed: false, runsFailed: false };
const REFRESH_EVENT = "verity:account-lists";

/** Asks every mounted useAccountLists to read the lists again, e.g. after a message was answered or a run started. */
export function refreshAccountLists(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(REFRESH_EVENT));
}

/**
 * GET /threads and GET /runs/mine while `signedIn`: on mount, on every refreshAccountLists() and when the window is focused
 * again (another device may have added to them). A read that fails keeps that list's last answer and is flagged. Signed out, the lists are empty.
 */
export function useAccountLists(signedIn: boolean): AccountLists {
  const [lists, setLists] = useState<AccountLists>(NONE);

  useEffect(() => {
    if (!signedIn) return;
    let ac = new AbortController();
    const load = () => {
      ac.abort(); // only the newest answer counts
      const mine = new AbortController();
      ac = mine;
      void Promise.all([listThreads(mine.signal), listMyRuns(mine.signal)]).then(([t, r]) => {
        if (mine.signal.aborted) return;
        setLists((prev) => ({
          threads: t.ok ? t.value : prev.threads,
          runs: r.ok ? r.value : prev.runs,
          threadsFailed: !t.ok,
          runsFailed: !r.ok,
        }));
      });
    };
    load();
    window.addEventListener(REFRESH_EVENT, load);
    window.addEventListener("focus", load);
    return () => {
      window.removeEventListener(REFRESH_EVENT, load);
      window.removeEventListener("focus", load);
      ac.abort();
    };
  }, [signedIn]);

  return signedIn ? lists : NONE;
}
