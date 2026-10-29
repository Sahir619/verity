import { FAMILIES } from "@/lib/contracts/presets";
import { Art } from "./Art";

// What Verity builds today and what comes next. Future rungs carry no numbers: nothing on them has run yet.
const RUNGS: { id: string; built: boolean; word: string; items: string[] }[] = [
  {
    id: "built",
    built: true,
    word: "Built",
    items: [`${FAMILIES.length} block families`, "RV32I CPU", "2-stage pipeline", "16-word cache"],
  },
  {
    id: "next",
    built: false,
    word: "Next",
    items: ["Custom small blocks", "RV32IM", "RV32IMC", "FP8 multiplier", "int8 requantize", "FIFO", "Arbiter", "CRC-32", "SECDED"],
  },
  {
    id: "later",
    built: false,
    word: "Later",
    items: ["Bring your own CPU as v1", "Assertions and coverage", "Layout", "FPGA"],
  },
];

/** RISC-V today, your chip next: Built, Next and Later as three plain columns. */
export function Ladder() {
  return (
    <section className="sec band" id="next" aria-labelledby="next-h">
      <Art file="art-bloom.webp" />
      <h2 id="next-h">RISC-V today. Your chip next.</h2>
      <ol className="row c3">
        {RUNGS.map((r) => (
          <li key={r.id} className={r.built ? "rung built" : "rung"}>
            <h3 className="label">
              <span className={r.built ? "sq" : "sq o"} aria-hidden />
              {r.word}
            </h3>
            <ul>
              {r.items.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      <p className="small ladder-note">
        The method stays the same as chips grow: blocks, locked golden models, proofs. A new block ships only after its golden
        model exists.
      </p>
    </section>
  );
}
