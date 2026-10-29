"use client";

import type { Research, Verdict } from "@/lib/sources/live";
import { TIER_WORD, VERDICT, citedSources, formatSmallUsd, plainDate, safeHref } from "./chat";

/**
 * The first line of a fact-checked reply: one fixed verdict, a shape and a word (never colour alone). The server's code
 * sets it from the checked class, not the model. "Could not check" carries Try again while the reply is the newest.
 */
export function VerdictLine({ verdict, onRetry }: { verdict: Verdict; onRetry?: () => void }) {
  const v = VERDICT[verdict];
  return (
    <div className="b-verdict">
      <span className="b-vsq" data-shape={v.shape} aria-hidden />
      <b>{v.word}</b>
      {verdict === "could_not_check" && onRetry ? (
        <button type="button" className="bx ghost sm" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

/** A citation chip or a source title. The address comes from the web, so it links only when it is a plain http or https address. */
function SourceLink({ url, className, label, title, children }: { url: string; className?: string; label?: string; title?: string; children: React.ReactNode }) {
  const href = safeHref(url);
  if (!href) return <span className={className}>{children}</span>;
  return (
    <a className={className} href={href} target="_blank" rel="noopener noreferrer nofollow" aria-label={label} title={title}>
      {children}
    </a>
  );
}

const times = (n: number) => (n === 1 ? "1 time" : `${n} times`);

/**
 * What this check cost. The server's usd is the total for this request (model calls plus searches) and is 0 when the
 * research was reused, so a reused check says it cost nothing new rather than quoting a figure it does not have.
 */
function costLine(research: Research): string {
  if (research.reused) return "This check reused saved research and cost nothing new.";
  const count = research.serperCredits + research.tavilyCredits;
  const searches = count === 1 ? "1 search" : `${count} searches`;
  return count > 0
    ? `This check cost ${formatSmallUsd(research.usd)} in all, counting the model calls and ${searches}.`
    : `This check cost ${formatSmallUsd(research.usd)} in model calls and ran no searches.`;
}

/** Sources shown at once. The rest sit under a dropdown, so the chip card and its Run button stay near the verdict. */
const SHOWN = 4;

function SourceList({ sources, label }: { sources: Research["sources"]; label: string }) {
  return (
    <ol className="b-srcs" aria-label={label}>
      {sources.map((s) => (
        <li key={s.n}>
          <span className="m b-sn">S{s.n}</span>
          <span className="b-sb">
            <SourceLink url={s.url}>{s.title || s.domain}</SourceLink>
            <small>
              {s.domain}
              {s.date ? `, ${s.date}` : ""}, {TIER_WORD[s.tier]}
            </small>
          </span>
        </li>
      ))}
    </ol>
  );
}

/**
 * The one dropdown behind a reply's plain words, labelled "Details" and closed until opened: the research, its sources and
 * cost, and how the words were checked. Nothing in it is dropped, only folded. A caller draws it only when it has content.
 */
export function ReplyDetails({ children }: { children: React.ReactNode }) {
  return (
    <details className="b-more b-det">
      <summary>Details</summary>
      <div className="b-det-body">{children}</div>
    </details>
  );
}

/**
 * What the check found, drawn inside a reply's Details: sentences each followed by a row of S1, S2 chips that open the
 * matching source, the known designs (labels from the server's fixed list for the family), the short source list, the exact
 * searches, and what it cost. The sentences and ids were checked by the server; a chip whose id is not in the list is not
 * drawn. Source text is shown as text only.
 */
export function ResearchBlock({ research }: { research: Research }) {
  const { sentences, sources, queries, techniques } = research;
  const when = research.createdAt ? plainDate(research.createdAt) : "";
  return (
    <div className="b-research">
      {research.status === "failed" ? <p>The searches did not finish, so this reply has no sources.</p> : null}
      {sentences
        .filter((s) => s.text.trim() !== "")
        .map((s, i) => {
          const cited = citedSources(research, s.sourceIds);
          return (
            <div key={i} className="b-sent">
              <p>{s.text}</p>
              {cited.length > 0 ? (
                <span className="b-cites" role="group" aria-label="Sources for this sentence">
                  {cited.map((src) => (
                    <SourceLink key={src.n} url={src.url} className="b-cite" label={`Source S${src.n}: ${src.title}, opens in a new tab`} title={`${src.title} (${src.domain})`}>
                      S{src.n}
                    </SourceLink>
                  ))}
                </span>
              ) : null}
            </div>
          );
        })}
      {techniques.length > 0 ? <p className="b-note">Known designs: {techniques.map((t) => t.label).join(", ")}.</p> : null}
      {sources.length > 0 ? <SourceList sources={sources.slice(0, SHOWN)} label="Sources" /> : null}
      {sources.length > SHOWN ? (
        <details className="b-more">
          <summary>{sources.length - SHOWN === 1 ? "Show 1 more source" : `Show ${sources.length - SHOWN} more sources`}</summary>
          <SourceList sources={sources.slice(SHOWN)} label="More sources" />
        </details>
      ) : null}
      {research.status === "partial" ? <p className="b-note b-dim">Some searches did not finish, so this list may be short.</p> : null}
      {research.pastRuns > 0 ? <p className="b-note b-dim">Verity built this before: {times(research.pastRuns)}.</p> : null}
      {queries.length > 0 ? <p className="b-note b-dim">Searched: {queries.map((q) => `"${q}"`).join(", ")}</p> : null}
      {research.reused ? <p className="b-note b-dim">Used saved research{when ? ` from ${when}` : ""}.</p> : null}
      <details className="b-more">
        <summary>What this check cost</summary>
        <p>{costLine(research)}</p>
      </details>
    </div>
  );
}
