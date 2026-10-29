import { CPU } from "./cpu-real";
import { numberWord, PROVIDER } from "./cpu-words";
import { LoopVideo } from "./LoopVideo";
import { hasLanding } from "./media";

// Posters are 1440 x 900 captures of the real CPU job on the bench.
const W = 1440;
const H = 900;

const STEPS = [
  {
    key: "ask",
    n: "01",
    title: "You ask",
    label: "The Verity bench: a request for an RV32I CPU and the CPU choices; building starts only after Confirm",
    body: `Verity splits the CPU into ${numberWord(CPU.blocksTotal)} blocks. Nothing runs until you press Confirm.`,
  },
  {
    key: "build",
    n: "02",
    title: "It builds and checks",
    label: `The CPU board: ${numberWord(CPU.blocksTotal)} RV32I blocks and the integrated core, all marked Passed`,
    body: "Nemotron writes the blocks in parallel, and each one runs against locked tests it cannot edit. Then Nemotron writes the core that joins them.",
  },
  {
    key: "upgrade",
    n: "03",
    title: "Then you upgrade it",
    label: "The benchmark comparison table against CPU v1: cycles for each benchmark and the total benchmark time, before and after",
    body: `With fast memory, a 2-stage pipeline runs the benchmarks in ${CPU.pipeline.benchTimeRatioText} the time of CPU v1.`,
  },
];

/** How it works, told by the real CPU job: you ask, it builds and checks, then you upgrade it. */
export function LoopSection() {
  return (
    <section className="sec how" id="how" aria-labelledby="how-h">
      <h2 id="how-h">Ask for a CPU. Watch it build one.</h2>
      <ol className="row c3 steps">
        {STEPS.map((s) => (
          <li key={s.key} className="step">
            <LoopVideo
              src={`/landing/loop-${s.key}.mp4`}
              poster={`/landing/poster-${s.key}.webp`}
              label={s.label}
              hasVideo={hasLanding(`loop-${s.key}.mp4`)}
              width={W}
              height={H}
            />
            <span className="label">{s.n}</span>
            <h3>{s.title}</h3>
            <p>{s.body}</p>
          </li>
        ))}
      </ol>
      <p className="small">
        Real CPU jobs, {PROVIDER}, {CPU.date}.
      </p>
    </section>
  );
}
