"use client";

import { useId, useState } from "react";

/** Replaces Run when the agent asks for an access code. Lives inside the request form, so it is not a form itself. */
export function AccessCode({ onSubmit, notice }: { onSubmit(code: string): Promise<string | null>; notice: string | null }) {
  const id = useId();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const trimmed = code.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    const message = await onSubmit(trimmed);
    setBusy(false);
    if (message) setError(message);
  };

  return (
    <div className="b-code">
      <label htmlFor={id} className="b-h">
        Access code
      </label>
      <p className="b-note">{notice ?? "Live runs need an access code, so every run stays on budget."}</p>
      <div className="b-code-row">
        <input
          id={id}
          type="password"
          className="b-bits"
          autoComplete="off"
          spellCheck={false}
          value={code}
          disabled={busy}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-e` : undefined}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <button type="button" className="btn sm" disabled={busy || code.trim() === ""} onClick={() => void submit()}>
          {busy ? "Checking..." : "Continue"}
        </button>
      </div>
      {error ? (
        <p id={`${id}-e`} className="b-note" role="alert">
          <span className="rkq fail" aria-hidden /> {error}
        </p>
      ) : null}
    </div>
  );
}
