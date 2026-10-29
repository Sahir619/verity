"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { Check, Copy, KeyRound, UserPlus, X } from "lucide-react";
import { createAdminUser } from "@/lib/sources/live";

export function AdminUserModal({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const emailId = useId();
  const passwordId = useId();
  const creditsId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [credits, setCredits] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{
    id: string;
    email: string;
    credits: number;
    initialPassword: string;
  } | null>(null);
  const [copied, setCopied] = useState(false);

  const handleClose = useCallback(() => {
    if (busy) return;
    setEmail("");
    setPassword("");
    setCredits(3);
    setError(null);
    setCreated(null);
    setCopied(false);
    onClose();
  }, [busy, onClose]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen && !busy) {
        handleClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, busy, handleClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanEmail = email.trim();
    if (!cleanEmail || !password || busy) return;

    setBusy(true);
    setError(null);

    const res = await createAdminUser(
      cleanEmail,
      password,
      Number.isFinite(credits) ? Math.max(0, credits) : 3
    );

    setBusy(false);

    if (!res.ok) {
      setError(res.error);
      return;
    }

    setCreated({
      id: res.userId,
      email: res.email,
      credits: res.credits,
      initialPassword: password,
    });
  };

  const handleCopyCredentials = async () => {
    if (!created) return;
    const text = `Email: ${created.email}\nPassword: ${created.initialPassword}\nCredits: ${created.credits}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard write failed or blocked
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="admin-modal-title"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
        background: "rgba(10, 8, 6, 0.75)",
        backdropFilter: "blur(4px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) {
          handleClose();
        }
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "460px",
          background: "var(--rk-panel)",
          border: "1px solid var(--rk-rule-2)",
          borderRadius: "14px",
          padding: "24px",
          boxShadow: "0 20px 40px rgba(0, 0, 0, 0.5)",
          color: "var(--rk-ink)",
          display: "flex",
          flexDirection: "column",
          gap: "18px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: "32px",
                height: "32px",
                borderRadius: "8px",
                background: "var(--rk-panel-2)",
                border: "1px solid var(--rk-rule)",
                color: "var(--rk-a)",
              }}
            >
              <UserPlus style={{ width: "16px", height: "16px" }} />
            </span>
            <div>
              <h2
                id="admin-modal-title"
                style={{
                  margin: 0,
                  fontSize: "16px",
                  fontWeight: 600,
                  fontFamily: "var(--font-body)",
                }}
              >
                Add Account
              </h2>
              <p
                style={{
                  margin: "2px 0 0",
                  fontSize: "12px",
                  color: "var(--rk-ink-3)",
                }}
              >
                Enter a new user with run credits (no public signups).
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={busy}
            aria-label="Close dialog"
            style={{
              background: "transparent",
              border: 0,
              color: "var(--rk-ink-3)",
              cursor: "pointer",
              padding: "4px",
              borderRadius: "6px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <X style={{ width: "18px", height: "18px" }} />
          </button>
        </div>

        {created ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div
              style={{
                padding: "14px",
                background: "var(--rk-ground)",
                border: "1px solid var(--rk-rule)",
                borderRadius: "10px",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                fontSize: "13px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--rk-ink-3)" }}>Email:</span>
                <span style={{ fontFamily: "var(--font-data)", color: "var(--rk-ink)" }}>
                  {created.email}
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--rk-ink-3)" }}>Password:</span>
                <span style={{ fontFamily: "var(--font-data)", color: "var(--rk-ink)" }}>
                  {created.initialPassword}
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--rk-ink-3)" }}>Credits:</span>
                <span style={{ fontFamily: "var(--font-data)", color: "var(--rk-a)" }}>
                  {created.credits} runs
                </span>
              </div>
            </div>

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={() => void handleCopyCredentials()}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "8px 14px",
                  fontSize: "13px",
                  borderRadius: "8px",
                  background: "var(--rk-panel-2)",
                  border: "1px solid var(--rk-rule)",
                  color: "var(--rk-ink)",
                  cursor: "pointer",
                }}
              >
                {copied ? (
                  <>
                    <Check style={{ width: "14px", height: "14px", color: "var(--rk-a)" }} />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy style={{ width: "14px", height: "14px" }} />
                    Copy Credentials
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={handleClose}
                style={{
                  padding: "8px 16px",
                  fontSize: "13px",
                  fontWeight: 600,
                  borderRadius: "8px",
                  background: "var(--rk-a)",
                  border: 0,
                  color: "var(--rk-on-a)",
                  cursor: "pointer",
                }}
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <form
            onSubmit={(e) => void handleSubmit(e)}
            style={{ display: "flex", flexDirection: "column", gap: "14px" }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <label
                htmlFor={emailId}
                style={{ fontSize: "12.5px", color: "var(--rk-ink-2)", fontWeight: 500 }}
              >
                Email
              </label>
              <input
                id={emailId}
                type="email"
                required
                autoComplete="off"
                placeholder="colleague@domain.com"
                value={email}
                disabled={busy}
                onChange={(e) => setEmail(e.target.value)}
                style={{
                  background: "var(--rk-ground)",
                  border: "1px solid var(--rk-rule)",
                  borderRadius: "8px",
                  padding: "8px 12px",
                  fontSize: "13.5px",
                  color: "var(--rk-ink)",
                  outline: "none",
                }}
              />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <label
                htmlFor={passwordId}
                style={{ fontSize: "12.5px", color: "var(--rk-ink-2)", fontWeight: 500 }}
              >
                Temporary Password
              </label>
              <input
                id={passwordId}
                type="password"
                required
                minLength={6}
                autoComplete="new-password"
                placeholder="At least 6 characters"
                value={password}
                disabled={busy}
                onChange={(e) => setPassword(e.target.value)}
                style={{
                  background: "var(--rk-ground)",
                  border: "1px solid var(--rk-rule)",
                  borderRadius: "8px",
                  padding: "8px 12px",
                  fontSize: "13.5px",
                  color: "var(--rk-ink)",
                  outline: "none",
                }}
              />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <label
                htmlFor={creditsId}
                style={{ fontSize: "12.5px", color: "var(--rk-ink-2)", fontWeight: 500 }}
              >
                Initial Run Credits
              </label>
              <input
                id={creditsId}
                type="number"
                min={0}
                max={1000}
                required
                value={credits}
                disabled={busy}
                onChange={(e) => setCredits(parseInt(e.target.value, 10) || 0)}
                style={{
                  background: "var(--rk-ground)",
                  border: "1px solid var(--rk-rule)",
                  borderRadius: "8px",
                  padding: "8px 12px",
                  fontSize: "13.5px",
                  color: "var(--rk-ink)",
                  outline: "none",
                }}
              />
            </div>

            {error ? (
              <div
                role="alert"
                style={{
                  fontSize: "12.5px",
                  color: "var(--rk-ink)",
                  background: "var(--rk-panel-2)",
                  border: "1px solid var(--rk-rule)",
                  padding: "8px 12px",
                  borderRadius: "8px",
                }}
              >
                {error}
              </div>
            ) : null}

            <div
              style={{
                display: "flex",
                gap: "10px",
                justifyContent: "flex-end",
                marginTop: "6px",
              }}
            >
              <button
                type="button"
                onClick={handleClose}
                disabled={busy}
                style={{
                  padding: "8px 14px",
                  fontSize: "13px",
                  borderRadius: "8px",
                  background: "transparent",
                  border: "1px solid var(--rk-rule)",
                  color: "var(--rk-ink-2)",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={busy || !email.trim() || !password}
                style={{
                  padding: "8px 16px",
                  fontSize: "13px",
                  fontWeight: 600,
                  borderRadius: "8px",
                  background: "var(--rk-a)",
                  border: 0,
                  color: "var(--rk-on-a)",
                  cursor: busy || !email.trim() || !password ? "not-allowed" : "pointer",
                  opacity: busy || !email.trim() || !password ? 0.6 : 1,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <KeyRound style={{ width: "14px", height: "14px" }} />
                {busy ? "Creating..." : "Create Account"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
