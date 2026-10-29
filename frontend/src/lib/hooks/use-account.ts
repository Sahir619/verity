"use client";

import { useEffect, useState } from "react";
import { isAccountsConfigured } from "@/lib/env";
import { currentAccount, getAccountProfile, onAccountChange, PROFILE_EVENT, subscribeSession } from "@/lib/sources/live";

/** `unknown` until the browser's saved session has been read (and refreshed). A build without accounts is `signed_out` from the start. */
export type Account = {
  status: "unknown" | "signed_in" | "signed_out";
  email: string | null;
  credits: number | null;
  isAdmin: boolean;
  /** Signed in by any means (account, access code, local admin): may start every kind of build, never charged credits. */
  canBuild: boolean;
};

const OUT: Account = { status: "signed_out", email: null, credits: null, isAdmin: false, canBuild: false };
const UNKNOWN: Account = { status: "unknown", email: null, credits: null, isAdmin: false, canBuild: false };

/**
 * Who is signed in with an account (Supabase). The Supabase client loads on the first call, never on the replay path,
 * and is not touched at all when the site is built without accounts.
 */
export function useAccount(): Account {
  const [email, setEmail] = useState<string | null | undefined>(isAccountsConfigured ? undefined : null);
  const [credits, setCredits] = useState<number | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [canBuild, setCanBuild] = useState(false);

  useEffect(() => {
    let live = true;
    const loadProfile = async () => {
      const p = await getAccountProfile();
      if (!live || !p) return;
      setCredits(p.credits);
      setIsAdmin(p.isAdmin);
      setCanBuild(p.canBuild ?? p.isAdmin);
      if (!email && p.email) {
        setEmail(p.email);
      }
    };

    if (isAccountsConfigured) {
      // The profile is read with no account too: an access code session or the local-admin visitor can build (canBuild).
      void currentAccount().then((e) => {
        if (live) {
          setEmail(e);
          void loadProfile();
        }
      });
      const off = onAccountChange((e) => {
        if (live) {
          setEmail(e);
          if (e) void loadProfile();
          else {
            setCredits(null);
            setIsAdmin(false);
            setCanBuild(false);
            void loadProfile();
          }
        }
      });
      const onProfile = () => {
        if (live) void loadProfile();
      };
      window.addEventListener(PROFILE_EVENT, onProfile);
      const offSession = subscribeSession(onProfile); // an access code accepted or dropped in this tab
      return () => {
        live = false;
        off();
        window.removeEventListener(PROFILE_EVENT, onProfile);
        offSession();
      };
    } else {
      void loadProfile();
      const onProfile = () => {
        if (live) void loadProfile();
      };
      window.addEventListener(PROFILE_EVENT, onProfile);
      const offSession = subscribeSession(onProfile); // an access code accepted or dropped in this tab
      return () => {
        live = false;
        window.removeEventListener(PROFILE_EVENT, onProfile);
        offSession();
      };
    }
  }, [email]);

  if (email === undefined) return UNKNOWN;
  return email === null
    ? { ...OUT, credits, isAdmin, canBuild }
    : { status: "signed_in", email, credits, isAdmin, canBuild };
}

