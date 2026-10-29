"use client";

import { useState } from "react";
import { Pin } from "lucide-react";
import { cn } from "@/lib/utils";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { PlanBlock } from "@/lib/contracts/types";
import { StatusSquare } from "./StatusSquare";

export type BlockSteerPanelProps = {
  block: PlanBlock;
  readOnly: boolean;
  pending?: boolean;
  onApply(change: { pins: string[]; variants: Record<string, string> }): void;
};

function initialVariants(block: PlanBlock): Record<string, string> {
  const out: Record<string, string> = {};
  for (const v of block.variants) out[v.id] = v.value;
  return out;
}

function sameSet(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x));
}

function Inner({ block, readOnly, pending, onApply }: BlockSteerPanelProps) {
  const [pins, setPins] = useState<string[]>(block.pins);
  const [variants, setVariants] = useState<Record<string, string>>(() => initialVariants(block));

  const locked = readOnly || Boolean(pending);
  const original = initialVariants(block);
  const dirty = !sameSet(pins, block.pins) || block.variants.some((v) => variants[v.id] !== original[v.id]);
  // A recording shows what was recorded: any pin, or a variant moved off its first option, was a human input.
  const recordedInput = block.pins.length > 0 || block.variants.some((v) => v.value !== v.options[0]);

  return (
    <div className="flex flex-col gap-4">
      {pending ? (
        <p className="inline-flex items-center gap-2 text-sm text-info" role="status">
          <StatusSquare status="running" size="sm" />
          Replanning
        </p>
      ) : null}
      {readOnly ? (
        <p className="text-xs text-fg-subtle">
          {recordedInput
            ? "Recorded human input. This is a recording, so the choices are read only."
            : "This is a recording, so the choices are read only. Steering works in live runs."}
        </p>
      ) : null}

      <fieldset className="flex flex-col gap-2" disabled={locked}>
        <legend className="mb-2 text-sm font-medium text-foreground">Constraint pins</legend>
        <div className="flex flex-wrap gap-2">
          {block.pinOptions.map((pin) => (
            <Toggle
              key={pin}
              variant="outline"
              size="sm"
              disabled={locked}
              pressed={pins.includes(pin)}
              onPressedChange={(on) => setPins((prev) => (on ? [...prev, pin] : prev.filter((p) => p !== pin)))}
              className={cn("h-8 gap-1.5 px-2.5 text-sm data-[state=on]:border-foreground data-[state=on]:bg-surface-3", readOnly && "disabled:opacity-100")}
            >
              <Pin aria-hidden />
              {pin}
            </Toggle>
          ))}
        </div>
      </fieldset>

      {block.variants.length > 0 ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium text-foreground">Variants</p>
          {block.variants.map((variant) => (
            <div key={variant.id} className="flex flex-col gap-1.5">
              <span className="text-xs text-fg-muted">{variant.label}</span>
              <ToggleGroup
                type="single"
                variant="outline"
                size="sm"
                spacing={0}
                disabled={locked}
                value={variants[variant.id]}
                aria-label={variant.label}
                onValueChange={(next) => {
                  if (next) setVariants((prev) => ({ ...prev, [variant.id]: next }));
                }}
              >
                {variant.options.map((option) => (
                  <ToggleGroupItem
                    key={option}
                    value={option}
                    className={cn("h-8 px-3 text-sm data-[state=on]:border-foreground data-[state=on]:bg-surface-3", readOnly && "disabled:opacity-100")}
                  >
                    {option}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
          ))}
        </div>
      ) : null}

      {readOnly ? null : (
        <button
          type="button"
          className="btn sm self-start"
          disabled={!dirty || pending}
          onClick={() => onApply({ pins, variants })}
        >
          {pending ? "Replanning" : "Apply and replan"}
        </button>
      )}

      <p className="text-xs text-fg-subtle">Pins and variant choices change the plan. The agent replans after you apply them.</p>
    </div>
  );
}

/** Pins and variant toggles for one plan block (the studio detail panel shows it in live runs). */
export function BlockSteerPanel(props: BlockSteerPanelProps) {
  // Remount when the block or its recorded values change so the local choices restart from the new values.
  const key = `${props.block.id}|${props.block.pins.join(",")}|${props.block.variants.map((v) => `${v.id}=${v.value}`).join(",")}`;
  return <Inner key={key} {...props} />;
}
