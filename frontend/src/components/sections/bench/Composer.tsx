"use client";

import { useId, type RefObject } from "react";
import { X } from "lucide-react";
import { MAX_CHARS } from "./chat";

/** Shown once the sentence is close to the limit, so the count is there when it matters and quiet otherwise. */
const COUNT_FROM = MAX_CHARS - 60;

/**
 * The chat box: one sentence about a chip, or, once a run has ended, a question about it. Enter sends, Shift+Enter adds a
 * line. Send is the page's one amber action until a chip card offers Run. The text lives in the parent so an example can fill it.
 * A picked version sits above the text as a removable chip ("v4"); it is not part of the text and does not count to the limit.
 */
export function Composer({
  text,
  onText,
  onSend,
  locked,
  busy,
  placeholder,
  label = "Describe a chip",
  busyWord = "Reading...",
  hint,
  context,
  inputRef,
}: {
  text: string;
  onText(text: string): void;
  onSend(): void;
  /** A run is in progress: the box waits until it ends. */
  locked: boolean;
  /** Verity is reading the last sentence. */
  busy: boolean;
  placeholder: string;
  /** The box's name for a screen reader. */
  label?: string;
  /** The Send button while Verity is working. */
  busyWord?: string;
  /** Replaces the keyboard hint, e.g. which version a question is about. */
  hint?: string;
  /** The version the next question is about, with the X that takes it off. */
  context?: { label: string; onRemove(): void } | null;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const id = useId();
  const canSend = !locked && !busy && text.trim() !== "";
  return (
    <form
      className="b-comp"
      data-locked={locked ? "" : undefined}
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      {context ? (
        <div className="b-ctx" role="group" aria-label="Attached to your question">
          <span className="b-ctx-chip">
            <span className="sr">Version </span>
            <span className="m">{context.label}</span>
            <button
              type="button"
              className="b-ctx-x"
              aria-label={`Remove ${context.label} from the question`}
              onClick={() => {
                context.onRemove();
                inputRef.current?.focus();
              }}
            >
              <X aria-hidden />
            </button>
          </span>
        </div>
      ) : null}
      <label htmlFor={id} className="sr">
        {label}
        {context ? `, about ${context.label}` : ""}
      </label>
      <textarea
        id={id}
        ref={inputRef}
        className="b-comp-in"
        rows={2}
        maxLength={MAX_CHARS}
        placeholder={placeholder}
        value={text}
        disabled={locked}
        aria-describedby={text.length >= COUNT_FROM ? `${id}-n` : undefined}
        onChange={(e) => onText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSend();
          }
        }}
      />
      <div className="b-comp-row">
        {text.length >= COUNT_FROM ? (
          <span id={`${id}-n`} className="b-count m">
            {text.length} of {MAX_CHARS} characters
          </span>
        ) : (
          <span className="b-comp-hint">{locked ? "" : (hint ?? "Enter to send, Shift+Enter for a new line")}</span>
        )}
        <button type="submit" className="bx pri" disabled={!canSend}>
          {busy ? busyWord : "Send"}
        </button>
      </div>
    </form>
  );
}
