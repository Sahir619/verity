"use client";

import { Fragment, useState } from "react";
import { BlockSteerPanel } from "@/components/domain/BlockSteerPanel";
import { CodeView } from "@/components/domain/CodeView";
import type { RunState, Spec } from "@/lib/contracts/types";
import type { SelectedEntity } from "@/lib/contracts/view-types";
import { useLive } from "@/lib/store";
import { goalText } from "../figure/derive";

/** The spec: the one human input every run starts from. */
export function SpecDetail({ spec }: { spec: Spec }) {
  return (
    <>
      <header className="dp-hero">
        <h2 className="dp-title">
          <span className="lbl">Spec</span>
          <span className="by">human input 1</span>
        </h2>
        <p className="dp-reason">{spec.title}</p>
      </header>
      <section className="dp-sec">
        <h3 className="dp-h">In plain English</h3>
        <blockquote className="dp-quote">{spec.text}</blockquote>
      </section>
      <section className="dp-sec">
        <h3 className="dp-h">Ports</h3>
        <div className="tbl-wrap">
          <table className="dtbl dp-tbl">
            <thead>
              <tr>
                <th scope="col">Port</th>
                <th scope="col">Direction</th>
                <th scope="col" className="num">
                  Width
                </th>
                <th scope="col" className="num">
                  Count
                </th>
              </tr>
            </thead>
            <tbody>
              {spec.ports.map((p) => (
                <tr key={p.name}>
                  <td className="mono">{p.name}</td>
                  <td>{p.dir === "in" ? "Input" : "Output"}</td>
                  <td className="num mono">{p.width}</td>
                  <td className="num mono">{p.count ?? 1}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="dp-sec">
        <h3 className="dp-h">Goal and limits</h3>
        <dl className="dp-dl">
          <dt>Goal</dt>
          <dd>{goalText(spec.priority)}</dd>
          <dt>Locked tests</dt>
          <dd className="mono">{spec.testCount}</dd>
          <dt>Line limit</dt>
          <dd className="mono">{spec.maxLines}</dd>
        </dl>
      </section>
    </>
  );
}

/** The golden model and its tests: locked, readable, never editable by the agent. */
export function GoldenDetail({ entity }: { entity: Extract<SelectedEntity, { kind: "golden" }> }) {
  const [showSource, setShowSource] = useState(false);
  return (
    <>
      <header className="dp-hero">
        <h2 className="dp-title">
          <span className="lbl">Golden model</span>
        </h2>
        <p className="mk win">Locked</p>
        <p className="dp-reason">
          {entity.testCount} tests. The agent can run them but cannot edit these files.
        </p>
      </header>
      {entity.files.length > 0 ? (
        <section className="dp-sec">
          <h3 className="dp-h">Files</h3>
          <ul className="dp-files">
            {entity.files.map((f) => (
              <li key={f} className="mono">
                {f}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {entity.source ? (
        <section className="dp-sec">
          <div className="dp-acts">
            <button type="button" className="btn ghost xs" aria-expanded={showSource} onClick={() => setShowSource((v) => !v)}>
              {showSource ? "Hide the golden model" : "Show the golden model"}
            </button>
          </div>
          {showSource ? <CodeView code={entity.source} language="python" filename="tests/golden_model.py" /> : null}
        </section>
      ) : null}
    </>
  );
}

/** One plan block. Live runs can steer it (pins and variants); a replay shows what was recorded. */
export function BlockDetail({ entity, run }: { entity: Extract<SelectedEntity, { kind: "block" }>; run: RunState }) {
  const { block, lastDiff, planRevision } = entity;
  const { steer, pendingSteers } = useLive();
  const live = run.mode === "live";
  const pending = pendingSteers.some((s) => (s.kind === "pin" || s.kind === "toggle") && s.blockId === block.id);
  const changed = lastDiff?.changed.includes(block.id) ?? false;
  const added = lastDiff?.added.includes(block.id) ?? false;

  const apply = (change: { pins: string[]; variants: Record<string, string> }) => {
    const pinSet = new Set(block.pins);
    const nextSet = new Set(change.pins);
    for (const pin of new Set([...pinSet, ...nextSet])) {
      if (pinSet.has(pin) !== nextSet.has(pin)) void steer({ kind: "pin", blockId: block.id, pin });
    }
    for (const v of block.variants) {
      const next = change.variants[v.id];
      if (next !== undefined && next !== v.value) void steer({ kind: "toggle", blockId: block.id, variantId: v.id, value: next });
    }
  };

  return (
    <>
      <header className="dp-hero">
        <h2 className="dp-title">
          <span className="lbl">{block.name}</span>
          <span className="by">plan block, rev {planRevision}</span>
        </h2>
        {block.reason ? <p className="dp-reason">{block.reason}</p> : null}
        {changed || added ? (
          <p className="dp-p">
            <b>{added ? "Added" : "Changed"} in rev {planRevision}</b>
            {run.plan.reason ? `: ${run.plan.reason}` : "."}
          </p>
        ) : null}
      </header>
      {live ? (
        <section className="dp-sec">
          <BlockSteerPanel block={block} readOnly={false} pending={pending} onApply={apply} />
        </section>
      ) : (
        <section className="dp-sec">
          <h3 className="dp-h">Choices in this recording</h3>
          <dl className="dp-dl">
            <dt>Pins</dt>
            <dd>{block.pins.length > 0 ? block.pins.join(", ") : "None"}</dd>
            {block.variants.map((v) => (
              <Fragment key={v.id}>
                <dt>{v.label}</dt>
                <dd>{v.value}</dd>
              </Fragment>
            ))}
          </dl>
        </section>
      )}
    </>
  );
}
