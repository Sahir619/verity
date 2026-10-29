import { Art } from "./Art";
import { FACTS } from "./problem-facts";

/** The problem, in three sourced facts. These are the page's only outside numbers, so the block says so. */
export function Problem() {
  return (
    <section className="sec band prob" id="problem" aria-labelledby="problem-h">
      <Art file="art-moth.webp" />
      <h2 id="problem-h">Writing Verilog was never the hard part. Knowing it is right is.</h2>
      <ul className="row c3">
        {FACTS.map((f) => (
          <li key={f.value} className="fact">
            <span className="fig">{f.value}</span>
            <p>{f.text}</p>
            <a className="fact-src" href={f.href} target="_blank" rel="noreferrer">
              {f.source}
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </li>
        ))}
      </ul>
      <p className="small row-note">Outside sources, not Verity runs.</p>
    </section>
  );
}
