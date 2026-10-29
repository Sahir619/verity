"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Users, X } from "lucide-react";
import { listAdminRuns, listAdminUsers, setAdminCredits, type AdminUserUsage } from "@/lib/sources/live";
import type { MyRun } from "@/lib/sources/live";

const SERVER_END_WORD: Record<string, string> = {
  done: "Done",
  stopped: "Stopped",
  cap_reached: "Spend limit",
  error: "Failed",
  timed_out: "Failed",
  queued: "Queued",
  running: "Running",
};

function isoLabel(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "";
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Every account, its credits and how many runs it has started (admin only, `GET /admin/users`). A row expands to
 * that user's runs (`GET /admin/runs`), and credits can be topped up in place (`POST /admin/credits`). Visibility
 * only: an admin cannot resume another account's run from here, since a resume is owner-only on the server.
 */
export function AdminUsersPanel({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [users, setUsers] = useState<AdminUserUsage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [runsByOwner, setRunsByOwner] = useState<Record<string, MyRun[] | "loading" | "failed">>({});
  const [creditInputs, setCreditInputs] = useState<Record<string, string>>({});
  const [savingOwner, setSavingOwner] = useState<string | null>(null);

  const load = () => {
    void listAdminUsers().then((res) => {
      if (!res.ok) {
        setError(res.error);
        return;
      }
      setError(null);
      setUsers(res.users);
    });
  };

  useEffect(() => {
    if (isOpen) load();
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const toggleExpand = async (owner: string) => {
    if (expanded === owner) {
      setExpanded(null);
      return;
    }
    setExpanded(owner);
    if (runsByOwner[owner]) return;
    setRunsByOwner((prev) => ({ ...prev, [owner]: "loading" }));
    const res = await listAdminRuns({ owner });
    setRunsByOwner((prev) => ({ ...prev, [owner]: res.ok ? res.runs : "failed" }));
  };

  const saveCredits = async (owner: string) => {
    const raw = creditInputs[owner];
    const credits = Number.parseInt(raw ?? "", 10);
    if (!Number.isFinite(credits) || credits < 0) return;
    setSavingOwner(owner);
    const res = await setAdminCredits({ owner }, { credits });
    setSavingOwner(null);
    if (res.ok) {
      setUsers((prev) => prev?.map((u) => (u.owner === owner ? { ...u, credits: res.credits } : u)) ?? prev);
      setCreditInputs((prev) => ({ ...prev, [owner]: "" }));
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="admin-users-title"
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
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "640px",
          maxHeight: "80vh",
          background: "var(--rk-panel)",
          border: "1px solid var(--rk-rule-2)",
          borderRadius: "14px",
          padding: "24px",
          boxShadow: "0 20px 40px rgba(0, 0, 0, 0.5)",
          color: "var(--rk-ink)",
          display: "flex",
          flexDirection: "column",
          gap: "14px",
          overflow: "hidden",
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
              <Users style={{ width: "16px", height: "16px" }} />
            </span>
            <h2 id="admin-users-title" style={{ margin: 0, fontSize: "16px", fontWeight: 600, fontFamily: "var(--font-body)" }}>
              Accounts
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            style={{ background: "transparent", border: 0, color: "var(--rk-ink-3)", cursor: "pointer", padding: "4px", borderRadius: "6px", display: "flex" }}
          >
            <X style={{ width: "18px", height: "18px" }} />
          </button>
        </div>

        <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
          {users === null && error === null ? (
            <p style={{ fontSize: "13px", color: "var(--rk-ink-3)", display: "flex", alignItems: "center", gap: "6px" }}>
              <Loader2 style={{ width: "14px", height: "14px" }} />
              Loading accounts
            </p>
          ) : error ? (
            <p role="alert" style={{ fontSize: "13px", color: "var(--rk-ink)" }}>
              {error}
            </p>
          ) : users && users.length === 0 ? (
            <p style={{ fontSize: "13px", color: "var(--rk-ink-3)" }}>No accounts yet.</p>
          ) : (
            users?.map((u) => (
              <div key={u.owner} style={{ border: "1px solid var(--rk-rule)", borderRadius: "10px", background: "var(--rk-ground)" }}>
                <button
                  type="button"
                  onClick={() => void toggleExpand(u.owner)}
                  style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "10px",
                    padding: "10px 12px",
                    background: "transparent",
                    border: 0,
                    cursor: "pointer",
                    color: "var(--rk-ink)",
                    textAlign: "left",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: "6px", minWidth: 0 }}>
                    {expanded === u.owner ? (
                      <ChevronDown style={{ width: "14px", height: "14px", flex: "none", color: "var(--rk-ink-3)" }} />
                    ) : (
                      <ChevronRight style={{ width: "14px", height: "14px", flex: "none", color: "var(--rk-ink-3)" }} />
                    )}
                    <span style={{ fontSize: "13.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {u.email ?? u.owner}
                    </span>
                    {u.role !== "user" ? (
                      <span style={{ fontSize: "10.5px", color: "var(--rk-a)", border: "1px solid var(--rk-rule)", borderRadius: "4px", padding: "0 5px", flex: "none" }}>
                        {u.role}
                      </span>
                    ) : null}
                  </span>
                  <span style={{ fontSize: "12px", color: "var(--rk-ink-3)", flex: "none", fontFamily: "var(--font-data)" }}>
                    {u.runsCount} {u.runsCount === 1 ? "run" : "runs"}
                  </span>
                </button>

                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "flex-end",
                    gap: "8px",
                    padding: "0 12px 10px",
                    fontSize: "12.5px",
                  }}
                >
                  <span style={{ color: "var(--rk-ink-3)" }}>Credits</span>
                  <span style={{ color: "var(--rk-a)", fontFamily: "var(--font-data)" }}>{u.credits}</span>
                  <input
                    type="number"
                    min={0}
                    placeholder="new value"
                    value={creditInputs[u.owner] ?? ""}
                    onChange={(e) => setCreditInputs((prev) => ({ ...prev, [u.owner]: e.target.value }))}
                    style={{
                      width: "84px",
                      background: "var(--rk-panel)",
                      border: "1px solid var(--rk-rule)",
                      borderRadius: "6px",
                      padding: "4px 8px",
                      fontSize: "12.5px",
                      color: "var(--rk-ink)",
                      outline: "none",
                    }}
                  />
                  <button
                    type="button"
                    disabled={!creditInputs[u.owner] || savingOwner === u.owner}
                    onClick={() => void saveCredits(u.owner)}
                    style={{
                      padding: "4px 10px",
                      fontSize: "12.5px",
                      fontWeight: 600,
                      borderRadius: "6px",
                      background: "var(--rk-a)",
                      border: 0,
                      color: "var(--rk-on-a)",
                      cursor: !creditInputs[u.owner] || savingOwner === u.owner ? "not-allowed" : "pointer",
                      opacity: !creditInputs[u.owner] || savingOwner === u.owner ? 0.6 : 1,
                    }}
                  >
                    {savingOwner === u.owner ? "Saving..." : "Set"}
                  </button>
                </div>

                {expanded === u.owner ? (
                  <div style={{ borderTop: "1px solid var(--rk-rule)", padding: "8px 12px 10px" }}>
                    {runsByOwner[u.owner] === "loading" ? (
                      <p style={{ fontSize: "12.5px", color: "var(--rk-ink-3)" }}>Loading runs</p>
                    ) : runsByOwner[u.owner] === "failed" ? (
                      <p style={{ fontSize: "12.5px", color: "var(--rk-ink-3)" }}>Could not load this account&apos;s runs.</p>
                    ) : (runsByOwner[u.owner] as MyRun[] | undefined)?.length === 0 ? (
                      <p style={{ fontSize: "12.5px", color: "var(--rk-ink-3)" }}>No runs yet.</p>
                    ) : (
                      <ul style={{ display: "flex", flexDirection: "column", gap: "4px", margin: 0, padding: 0, listStyle: "none" }}>
                        {(runsByOwner[u.owner] as MyRun[]).map((r) => (
                          <li
                            key={r.id}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                              gap: "8px",
                              fontSize: "12.5px",
                            }}
                          >
                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--rk-ink-2)" }}>
                              {r.title || "Untitled run"}
                            </span>
                            <span style={{ color: "var(--rk-ink-3)", flex: "none" }}>
                              {[SERVER_END_WORD[r.status ?? ""] ?? "Unknown", isoLabel(r.createdAt)].filter(Boolean).join(", ")}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
