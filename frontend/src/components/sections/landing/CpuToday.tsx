import { Art } from "./Art";
import { CPU } from "./cpu-real";
import { numberWord, ofPair, PROVIDER } from "./cpu-words";

const allPassed = CPU.blocksPassed === CPU.blocksTotal;
const shortTitle = (t: string) => t.replace(/^RV32I\s+/, "");
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const FIGURES: { value: string; text: string; earned?: boolean }[] = [
  { value: ofPair([CPU.blocksPassed, CPU.blocksTotal]), text: "blocks passed", earned: allPassed },
  { value: ofPair(CPU.core.programs), text: "RV32I programs" },
  { value: ofPair(CPU.core.riscvFormal), text: "riscv-formal checks, CPU v1" },
  { value: CPU.pipeline.benchTimeRatioText, text: "pipelined benchmark time, fast memory" },
];

// A block whose smaller version was not proven equal to v1: the core takes its v1, and the page says so.
const notProven = CPU.blocks.filter((b) => b.usesV1NotProven).map((b) => shortTitle(b.title));
const notProvenText =
  notProven.length === 0
    ? ""
    : notProven.length === 1
      ? ` The ${notProven[0]}'s smaller version was not proven, so the core uses its v1.`
      : ` The smaller versions of ${notProven.join(" and ")} were not proven, so the core uses their v1.`;

/** Today: the RISC-V CPU Verity built from blocks, in four figures. Every figure from cpu-real.json. */
export function CpuToday() {
  return (
    <section className="sec cpu" id="cpu" aria-labelledby="cpu-h">
      <Art file="art-strata.webp" />
      <h2 id="cpu-h">A RISC-V CPU, built from {numberWord(CPU.blocksTotal)} blocks.</h2>
      <ul className="row c4">
        {FIGURES.map((f) => (
          <li key={f.text}>
            <div className={f.earned ? "fig a" : "fig"}>{f.value}</div>
            <p>
              {f.earned ? <span className="sq" aria-hidden /> : null}
              {f.text}
            </p>
          </li>
        ))}
      </ul>
      <p className="small row-note">
        Real CPU jobs, {PROVIDER}, {CPU.date}, against our own CPU v1.{notProvenText} {cap(numberWord(CPU.perJobSpendUsd.length))} jobs
        cost {CPU.totalSpendText}.
      </p>
    </section>
  );
}
