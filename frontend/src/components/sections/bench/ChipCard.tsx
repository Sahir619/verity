"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { FAMILIES, OUT_OF_RANGE, familyById, isDefaultKnobs, knobDefaults, type KnobValue } from "@/lib/contracts/presets";
import type { Priority } from "@/lib/contracts/types";
import type { LiveHealth } from "@/lib/contracts/view-types";
import { AccessCode } from "./AccessCode";
import { notReady, type ChatMessage } from "./chat";
import { KnobField, PRIORITIES, blocker, type RunRequest } from "./RequestModule";

type CardMessage = Extract<ChatMessage, { kind: "card" }>;

/** One plain line per kind of chip for the picker (the contract's own summaries use engineering terms). Unknown ids show the name only. */
const PLAIN_LINE: Record<string, string> = {
  mac4: "Multiplies pairs of numbers and adds them up, several at once.",
  add8: "Adds two numbers.",
  cnt8: "Counts up, then starts again from zero.",
  fsm_seq: "Spots a pattern in a stream of bits.",
};

/** "What Verity cannot build": the last line of a card, and of a reply that refused a sentence. */
export function CannotBuild() {
  return (
    <details className="b-more">
      <summary>What Verity cannot build</summary>
      <ul className="b-more-list">
        {OUT_OF_RANGE.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    </details>
  );
}

/**
 * Verity's answer: the chip it understood, all at once on a compact grid. The kind as pills with one plain line, the
 * kind's knobs side by side, and the goal with Run on one line. There is no edit mode. Only the newest card is live; a
 * sent card stays as a short record of what ran.
 */
export function ChipCard({
  msg,
  live,
  health,
  needsCode,
  codeNotice,
  starting,
  startError,
  onSubmitCode,
  onRun,
}: {
  msg: CardMessage;
  /** The newest message and not sent yet: the kind, the knobs, the goal and Run work. */
  live: boolean;
  health: LiveHealth;
  needsCode: boolean;
  codeNotice: string | null;
  starting: boolean;
  startError: string | null;
  onSubmitCode(code: string): Promise<string | null>;
  onRun(req: RunRequest): void;
}) {
  const id = useId();
  const [familyId, setFamilyId] = useState(msg.familyId);
  const [knobs, setKnobs] = useState<Record<string, KnobValue>>(msg.knobs);
  const [priority, setPriority] = useState<Priority>(msg.priority);

  if (!live) {
    const f = familyById(msg.familyId);
    const goal = PRIORITIES.find((p) => p.id === msg.priority)?.label ?? "Smaller";
    return (
      <div className="b-card" data-quiet="">
        <p className="b-card-t">{f ? f.describe(msg.knobs) : "Chip"}</p>
        <p className="b-card-s">{msg.sent ? `Goal: ${goal}. Sent to Verity.` : "Not run."}</p>
      </div>
    );
  }

  const family = familyById(familyId) ?? FAMILIES[0];
  const ready = health.status === "ready";
  const why = blocker(family, knobs, health);
  const canRun = ready && !starting && !needsCode && why === null;

  const pickFamily = (next: string) => {
    const f = familyById(next);
    if (!f) return;
    setFamilyId(f.id);
    setKnobs(knobDefaults(f));
  };

  return (
    <form
      className="b-card"
      data-busy={starting ? "" : undefined}
      aria-label={`Chip: ${family.describe(knobs)}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (!canRun) return;
        // The check belongs to the kind that was read; picking another kind drops it. Edited knobs are left for the agent to judge.
        const researchId = family.id === msg.familyId ? msg.check?.research?.id : undefined;
        onRun({ familyId: family.id, knobs, priority, title: family.describe(knobs), sendKnobs: health.features?.includes("params") === true, researchId });
      }}
    >
      <div className="b-card-top">
        <p className="b-card-t">{family.describe(knobs)}</p>
        {!isDefaultKnobs(family, knobs) ? (
          <button type="button" className="b-link" disabled={starting} onClick={() => setKnobs(knobDefaults(family))}>
            Reset to the defaults
          </button>
        ) : null}
      </div>

      <div className="b-kind">
        <span className="b-lab" id={`${id}-kind`}>
          Kind
        </span>
        <div className="b-kind-b">
          <div className="b-pills" role="radiogroup" aria-labelledby={`${id}-kind`}>
            {FAMILIES.map((f) => (
              <label key={f.id} className="b-pill">
                <input type="radio" name={`${id}-fam`} value={f.id} checked={f.id === family.id} disabled={starting} onChange={() => pickFamily(f.id)} />
                <span>{f.name}</span>
              </label>
            ))}
          </div>
          {PLAIN_LINE[family.id] ? <p className="b-kind-s">{PLAIN_LINE[family.id]}</p> : null}
        </div>
      </div>

      <div className="b-knobs">
        {family.knobs.map((k) => (
          <KnobField key={`${family.id}-${k.id}`} k={k} value={knobs[k.id]} disabled={starting} onChange={(v) => setKnobs((prev) => ({ ...prev, [k.id]: v }))} />
        ))}
      </div>

      <div className="b-goal">
        <span className="b-lab" id={`${id}-goal`}>
          Goal
        </span>
        <div className="b-seg" role="radiogroup" aria-labelledby={`${id}-goal`}>
          {PRIORITIES.map((p) => (
            <label key={p.id}>
              <input type="radio" name={`${id}-goal`} value={p.id} checked={priority === p.id} disabled={starting} onChange={() => setPriority(p.id)} />
              <span>{p.label}</span>
            </label>
          ))}
        </div>
        <button type="submit" className="bx pri" disabled={!canRun} aria-describedby={why ? `${id}-why` : undefined}>
          {starting ? "Starting..." : "Run"}
        </button>
      </div>

      {health.status === "checking" ? (
        <p className="b-note">Checking whether live runs are open.</p>
      ) : !ready ? (
        <div className="b-card-stop">
          <p className="b-note">{notReady(health)}</p>
          <Link prefetch={false} href="/studio?run=mac4-real" className="bx ghost sm">
            Watch a recorded run
          </Link>
        </div>
      ) : needsCode ? (
        <AccessCode onSubmit={onSubmitCode} notice={codeNotice} />
      ) : null}
      {why && ready ? (
        <p id={`${id}-why`} className="b-note">
          {why}
        </p>
      ) : null}
      {startError && ready ? (
        <p className="b-note" role="alert">
          <span className="rkq fail" aria-hidden /> {startError}
        </p>
      ) : null}

      <div className="b-card-foot">
        <CannotBuild />
        {ready && !needsCode ? <p className="b-card-s b-cost">Takes a few minutes.</p> : null}
      </div>
    </form>
  );
}
