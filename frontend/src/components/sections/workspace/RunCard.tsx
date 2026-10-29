import Link from "next/link";
import { Sq } from "../studio/loop/LoopModule";
import { formatSmallUsd } from "../bench/chat";
import { pctText, winnerWords, type WorkRun } from "./derive";

function startedAt(ms: number): string {
  const d = new Date(ms);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * One run: its name (a link that opens the run in the bench), whether it ended well, which version won, how the winner
 * compares with that run's own v1, the sign-off seeds, and when it started and what it spent (the footer, pinned to the
 * card's bottom so footers line up across a row). Everything past the name comes from the server's
 * run data; a run the server did not return says so instead of leaving gaps.
 */
export function RunCard({
  run,
  loading,
  resuming = false,
  onResume,
}: {
  run: WorkRun;
  loading: boolean;
  /** True while this card's own resume call is in flight. */
  resuming?: boolean;
  /** Present only when the run can be resumed (status "stopped"): continues it from its checkpoint. */
  onResume?: (id: string) => void;
}) {
  const outcome = run.server?.outcome;
  const won = winnerWords(run);
  const keptV1 = run.server?.winner?.label === "v1";
  const area = pctText(outcome?.areaVsV1);
  const clock = pctText(outcome?.clockVsV1);
  const power = pctText(outcome?.powerVsV1);
  const changes = [
    { word: "Area", text: area },
    { word: "Clock", text: clock },
    { word: "Power", text: power },
  ].filter((c): c is { word: string; text: string } => c.text !== null);

  return (
    <li className="w-card" data-passed={run.passed ? "" : undefined}>
      <div className="w-card-top">
        <Link prefetch={false} href={`/bench?run=${encodeURIComponent(run.id)}`} className="w-card-t">
          {run.title}
        </Link>
        <p className="w-card-s">
          <Sq kind={run.status.kind} />
          <span>
            {run.status.word}
            {won ? `, ${won}` : ""}
          </span>
        </p>
      </div>
      {run.server?.status === "stopped" && onResume ? (
        <button
          type="button"
          className="btn sm"
          disabled={resuming}
          onClick={() => onResume(run.id)}
        >
          {resuming ? "Resuming..." : "Resume"}
        </button>
      ) : null}
      {loading ? (
        <p className="w-card-d" role="status">
          Loading results
        </p>
      ) : outcome ? (
        <>
          {keptV1 ? (
            <p className="w-card-d">No version beat v1</p>
          ) : changes.length > 0 ? (
            <p className="w-card-d">
              {changes.map((c, i) => (
                <span key={c.word}>
                  {i > 0 ? ", " : ""}
                  {c.word} <b>{c.text}</b>
                </span>
              ))}{" "}
              vs v1
            </p>
          ) : null}
          {outcome.signoff ? (
            <p className="w-card-d">
              Sign-off: <b>{outcome.signoff.seedsPassed} of {outcome.signoff.seedsRun}</b> test runs passed
            </p>
          ) : null}
        </>
      ) : run.missing === "not_on_server" ? (
        <p className="w-card-d">Not in the server&apos;s latest runs</p>
      ) : run.missing === "not_loaded" ? (
        <p className="w-card-d">Results could not be loaded</p>
      ) : run.server ? (
        <p className="w-card-d">
          {run.server.status === "queued" || run.server.status === "running" ? "No results yet" : "No results saved for this run"}
        </p>
      ) : null}
      <p className="w-card-f">
        Started {startedAt(run.startedAt)}
        {outcome && !loading ? `, spent ${formatSmallUsd(outcome.usd)}` : ""}
      </p>
    </li>
  );
}
