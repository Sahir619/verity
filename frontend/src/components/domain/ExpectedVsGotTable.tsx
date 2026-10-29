import { cn } from "@/lib/utils";
import type { Verification } from "@/lib/contracts/types";

export type ExpectedVsGotTableProps = { verification: Verification; attemptLabel?: string };

/** Renders text with the characters that differ from `other` in bold (extra characters count as different). */
function Mismatch({ text, other, className }: { text: string; other: string; className?: string }) {
  return (
    <span className={cn("mono", className)}>
      {Array.from(text).map((ch, i) => (
        <span key={i} className={ch !== other[i] ? "evg-diff" : undefined}>
          {ch}
        </span>
      ))}
    </span>
  );
}

/**
 * One simulation attempt: summary, failing cases (expected vs got), the agent's diagnosis and the lint result.
 * Drawn in the studio's datasheet style (.vs): a marker square plus a word, never colour alone.
 */
export function ExpectedVsGotTable({ verification, attemptLabel }: ExpectedVsGotTableProps) {
  const { lint, lintLog, passed, total, failures, diagnosis } = verification;
  const lintFailed = lint === "fail";
  const allPassed = !lintFailed && failures.length === 0 && passed === total;
  const heading = lintFailed
    ? `${attemptLabel ?? "Attempt"}: lint failed`
    : `${attemptLabel ?? "Result"}: ${passed} of ${total} passed`;

  return (
    <section className="evg" aria-label={attemptLabel ?? "Test result"}>
      <div className="evg-h">
        <h3 className="evg-t">{heading}</h3>
        <span className={cn("mk", allPassed ? "pass" : "fail")}>{lintFailed ? "Lint fail" : allPassed ? "Pass" : "Fail"}</span>
      </div>

      {lintFailed ? (
        <div className="evg-lint">
          <p>Lint failed. Simulation did not run.</p>
          {lintLog ? <pre className="cli">{lintLog}</pre> : null}
        </div>
      ) : allPassed ? (
        <p className="evg-ok">{`All ${total} tests passed.`}</p>
      ) : (
        <div className="tbl-wrap evg-tbl">
          <table className="dtbl">
            <thead>
              <tr>
                <th scope="col">Inputs</th>
                <th scope="col">Expected</th>
                <th scope="col">Got</th>
                <th scope="col" className="num">
                  Cycle
                </th>
              </tr>
            </thead>
            {failures.map((c) => (
              <tbody key={c.name}>
                <tr className="evg-name">
                  <th scope="rowgroup" colSpan={4} className="mono">
                    {c.name}
                  </th>
                </tr>
                <tr>
                  <td className="mono evg-wrap">{c.inputs}</td>
                  <td>
                    <Mismatch text={c.expected} other={c.got} />
                  </td>
                  <td>
                    <Mismatch text={c.got} other={c.expected} className="evg-got" />
                  </td>
                  <td className="num mono">{c.cycle ?? ""}</td>
                </tr>
              </tbody>
            ))}
          </table>
        </div>
      )}

      {diagnosis ? (
        <p className="evg-dx">
          <b>Agent&apos;s diagnosis:</b> {diagnosis}
        </p>
      ) : null}
      <p className="evg-lintline">{`Lint: ${lint} (Verilator)`}</p>
    </section>
  );
}
