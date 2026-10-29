"use client";

import { useId } from "react";
import { isDefaultKnobs, knobError, type Family, type KnobDef, type KnobValue } from "@/lib/contracts/presets";
import type { Priority } from "@/lib/contracts/types";
import type { LiveHealth } from "@/lib/contracts/view-types";

// The request parts the bench chat card reuses: the goal choice, one knob control, and why Run is blocked.

export const PRIORITIES: { id: Priority; label: string; hint: string }[] = [
  { id: "area", label: "Smaller", hint: "area" },
  { id: "speed", label: "Faster", hint: "clock" },
  { id: "power", label: "Lower power", hint: "estimate" },
];

export type RunRequest = {
  familyId: string;
  knobs: Record<string, KnobValue>;
  priority: Priority;
  title: string;
  sendKnobs: boolean;
  /** The check made before Run. The agent ignores it if it does not fit the chip that runs. */
  researchId?: string;
};

export function KnobField({ k, value, disabled, onChange }: { k: KnobDef; value: KnobValue; disabled: boolean; onChange(v: KnobValue): void }) {
  const id = useId();
  const err = knobError(k, value);
  if (k.kind === "int") {
    const n = typeof value === "number" ? value : k.default;
    return (
      <div className="b-knob">
        <div className="b-knob-h">
          <label htmlFor={id}>{k.label}</label>
          <output htmlFor={id} className="m">
            {n} <span>{k.unit}</span>
          </output>
        </div>
        <input
          id={id}
          type="range"
          min={k.min}
          max={k.max}
          step={1}
          value={n}
          disabled={disabled}
          aria-valuetext={`${n} ${k.unit}`}
          style={{ "--p": `${((n - k.min) / (k.max - k.min)) * 100}%` } as React.CSSProperties}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <div className="b-knob-r m" aria-hidden>
          <span>{k.min}</span>
          <span>{k.max}</span>
        </div>
      </div>
    );
  }
  if (k.kind === "bool") {
    const on = value === true;
    return (
      <div className="b-knob b-knob-bool">
        <span className="b-knob-l" id={`${id}-l`}>
          {k.label}
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby={`${id}-l`}
          className="b-switch"
          disabled={disabled}
          onClick={() => onChange(!on)}
        >
          <span className="rkq" data-on={on ? "" : undefined} aria-hidden />
          {on ? k.on : k.off}
        </button>
      </div>
    );
  }
  const s = typeof value === "string" ? value : k.default;
  return (
    <div className="b-knob">
      <div className="b-knob-h">
        <label htmlFor={id}>{k.label}</label>
        <span className="m b-knob-u">
          {k.minLen} to {k.maxLen} bits
        </span>
      </div>
      <input
        id={id}
        className="b-bits m"
        inputMode="numeric"
        autoComplete="off"
        spellCheck={false}
        maxLength={k.maxLen}
        value={s}
        disabled={disabled}
        aria-invalid={err ? true : undefined}
        aria-describedby={err ? `${id}-e` : undefined}
        onChange={(e) => onChange(e.target.value.replace(/[^01]/g, ""))}
      />
      {err ? (
        <p id={`${id}-e`} className="b-note" role="alert">
          <span className="rkq fail" aria-hidden /> {err}
        </p>
      ) : null}
    </div>
  );
}

/** Why Run cannot start right now, or null when it can. */
export function blocker(f: Family, knobs: Record<string, KnobValue>, health: LiveHealth): string | null {
  const bad = f.knobs.map((k) => knobError(k, knobs[k.id])).find((e) => e !== null);
  if (bad) return bad;
  if (health.features?.includes("params")) return null;
  if (!f.liveAtDefaults) return `${f.name} runs land with knob support in the backend. The MAC array at its default knobs runs today.`;
  if (!isDefaultKnobs(f, knobs)) return "Custom knob values land with the backend. Reset to the defaults to run today.";
  return null;
}
