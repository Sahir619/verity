"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { isAccountsConfigured } from "@/lib/env";
import { useAccount } from "@/lib/hooks/use-account";
import { useLiveSession } from "@/lib/hooks/use-live-session";
import { createSession, signIn } from "@/lib/sources/live";
import { AccessCode } from "../bench/AccessCode";
import { Logo } from "../shared/Logo";
import { STUDIO_ROOT } from "../studio/studio-root";
import "../bench/bench.css";
import "./signin.css";

/** Where to go after signing in: the `next` path when it stays on this site (and is not this page), else the bench. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/signin") || next.includes("\\")) return "/bench";
  return [...next].some((c) => c.charCodeAt(0) < 32) ? "/bench" : next;
}

/**
 * /signin: the one way into the live bench for a reader with an account. Email and password only, and no sign-up: the Verity
 * team makes each account and gives it out. "Have an access code?" opens the judge code form that the bench has always had.
 * Already signed in (an account, or an access code accepted earlier in this tab), or just signed in, the page forwards to `?next=`.
 */
export function SignInView() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const account = useAccount();
  const codeSession = useLiveSession();
  const ids = useId();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A build without accounts has only the code, so its form is open from the start.
  const [codeOpen, setCodeOpen] = useState(!isAccountsConfigured);
  const leaving = useRef(false);

  const signedIn = account.status === "signed_in" || codeSession; // an account, or an access code already accepted in this tab
  useEffect(() => {
    if (!signedIn || leaving.current) return;
    leaving.current = true;
    router.replace(next);
  }, [signedIn, next, router]);

  const go = () => {
    if (leaving.current) return;
    leaving.current = true;
    router.replace(next);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || email.trim() === "" || password === "") return;
    setBusy(true);
    setError(null);
    const res = await signIn(email.trim(), password);
    if (res.ok) return go(); // stays busy until the next page takes over
    setBusy(false);
    setError(res.error);
  };

  const errId = `${ids}-e`;
  return (
    <div className={`${STUDIO_ROOT} signin`}>
      <header className="si-top">
        <Link prefetch={false} href="/" className="b-brand" aria-label="Verity home">
          <Logo />
        </Link>
      </header>
      <main className="si-main">
        <h1>Sign in to Verity</h1>
        <p className="si-lede">{isAccountsConfigured ? "Use the account the Verity team gave you." : "Live runs here need an access code."}</p>
        {isAccountsConfigured ? (
          <form className="si-form" onSubmit={(e) => void submit(e)}>
            <div className="si-field">
              <label htmlFor={`${ids}-email`}>Email</label>
              <input
                id={`${ids}-email`}
                className="si-input"
                type="email"
                name="email"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="you@example.com"
                value={email}
                disabled={busy}
                aria-describedby={error ? errId : undefined}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="si-field">
              <label htmlFor={`${ids}-password`}>Password</label>
              <input
                id={`${ids}-password`}
                className="si-input"
                type="password"
                name="password"
                autoComplete="current-password"
                value={password}
                disabled={busy}
                aria-describedby={error ? errId : undefined}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error ? (
              <p id={errId} className="si-err" role="alert">
                <span className="rkq fail" aria-hidden /> {error}
              </p>
            ) : null}
            <button type="submit" className="btn si-go" disabled={busy || email.trim() === "" || password === ""}>
              {busy ? "Signing in..." : "Sign in"}
            </button>
          </form>
        ) : null}
        <div className="si-code">
          {isAccountsConfigured ? (
            <button type="button" className="b-link" aria-expanded={codeOpen} aria-controls={`${ids}-code`} onClick={() => setCodeOpen((open) => !open)}>
              Have an access code?
            </button>
          ) : null}
          {codeOpen ? (
            <div id={`${ids}-code`}>
              <AccessCode
                notice="Enter the access code the Verity team gave you."
                onSubmit={async (code) => {
                  const res = await createSession(code);
                  if (!res.ok) return res.error;
                  go();
                  return null;
                }}
              />
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}
