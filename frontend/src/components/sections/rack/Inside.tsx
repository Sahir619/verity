import { Art } from "../landing/Art";

// The four checks that stand between the agent and a pass. No numbers: each one is a rule, not a result.
const CHECKS = [
  { title: "Locked tests", text: "Written from a golden model before any Verilog exists. The agent cannot edit them." },
  { title: "Planted bugs", text: "We plant bugs on purpose and count how many the tests catch." },
  { title: "Measured", text: "Yosys and OpenSTA measure area and clock, always against our own v1." },
  { title: "Proven", text: "Yosys EQY proves a smaller version still does what v1 does." },
];

/** "Why trust it": four checks, one sentence each. */
export function Inside() {
  return (
    <section className="sec band" id="inside" aria-labelledby="inside-h">
      <Art file="art-weaver.webp" />
      <h2 id="inside-h">Four checks the agent cannot change.</h2>
      <ul className="row c4">
        {CHECKS.map((c) => (
          <li key={c.title} className="check">
            <h3>{c.title}</h3>
            <p>{c.text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
