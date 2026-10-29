"use client";

/**
 * The small RISC-V CPU first (a CPU job, not a family; "cpu" is only its list key), then one example per family in
 * FAMILIES (presets.ts), each inside that family's knob limits, so each one reads in range:
 * mac4 4 lanes x 8-bit x 16 cycles, add8 16 bits, cnt8 12 bits, fsm_seq the 4-bit pattern 1011.
 */
export const EXAMPLES: { family: string; label: string; text: string }[] = [
  { family: "cpu", label: "A small RISC-V CPU", text: "Build me a small RV32I RISC-V CPU" },
  { family: "mac4", label: "4-lane multiply-accumulate, 8-bit", text: "A 4-lane multiply-accumulate, 8-bit inputs, 16 cycles" },
  { family: "add8", label: "16-bit adder with carry", text: "A 16-bit adder with carry in and carry out" },
  { family: "cnt8", label: "12-bit counter", text: "A 12-bit counter with an enable input" },
  { family: "fsm_seq", label: "Detect the pattern 1011", text: "Detect the bit pattern 1011 on a serial input" },
];

/** Example prompts under the chat box. A tap fills the box and focuses it; it never sends by itself. */
export function ExamplePrompts({ onPick, onPickYourself }: { onPick(text: string): void; onPickYourself(): void }) {
  return (
    <div className="b-ex">
      <ul className="b-ex-list" aria-label="Examples">
        {EXAMPLES.map((ex) => (
          <li key={ex.family}>
            <button type="button" className="b-chip" title={ex.text} onClick={() => onPick(ex.text)}>
              {ex.label}
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="b-link" onClick={onPickYourself}>
        Or pick the chip and its settings yourself
      </button>
    </div>
  );
}
