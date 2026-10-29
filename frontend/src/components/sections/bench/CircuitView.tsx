"use client";

import { memo, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import dagre from "@dagrejs/dagre";
import { Maximize, Minus, Plus } from "lucide-react";
import { useReducedMotion } from "@/lib/hooks/use-reduced-motion";
import type { Circuit, CircuitKind } from "./artifacts";

/** The plain word for each part. A merged part ("add x3") says how many it stands for. */
const KIND_WORD: Record<CircuitKind, string> = {
  port: "port",
  reg: "register",
  add: "adder",
  mul: "multiplier",
  mux: "mux",
  cmp: "compare",
  logic: "logic",
  submodule: "block",
};

type PartData = {
  kind: CircuitKind;
  title: string;
  bits: string;
  /** The walkthrough points at this part (the agent's highlight). */
  agent: boolean;
  /** The reader picked this part (their own selection, apart from the agent's). */
  picked: boolean;
  w: number;
  h: number;
};
type PartNode = Node<PartData, "part">;

const SIZE: Record<CircuitKind, { w: number; h: number }> = {
  port: { w: 84, h: 34 },
  reg: { w: 92, h: 42 },
  add: { w: 86, h: 42 },
  mul: { w: 96, h: 42 },
  mux: { w: 70, h: 46 },
  cmp: { w: 86, h: 40 },
  logic: { w: 96, h: 38 },
  submodule: { w: 104, h: 44 },
};

function titleOf(n: Circuit["nodes"][number], dir: "in" | "out" | undefined): string {
  if (n.kind === "port") return `${dir === "out" ? "out" : dir === "in" ? "in" : "port"} ${n.label}`;
  const merged = /\bx(\d+)$/.exec(n.label);
  const word = n.kind === "logic" || n.kind === "submodule" ? n.label.split(" ")[0] : KIND_WORD[n.kind];
  return merged ? `${word} x${merged[1]}` : word;
}

/** The outline of each kind, so the parts differ by shape as well as by their word. */
function Outline({ kind, w, h }: { kind: CircuitKind; w: number; h: number }) {
  const s = { vectorEffect: "non-scaling-stroke" as const };
  switch (kind) {
    case "mux":
      return <polygon points={`1,1 ${w - 1},${h * 0.22} ${w - 1},${h * 0.78} 1,${h - 1}`} {...s} />;
    case "cmp":
      return <polygon points={`10,1 ${w - 10},1 ${w - 1},${h / 2} ${w - 10},${h - 1} 10,${h - 1} 1,${h / 2}`} {...s} />;
    case "add":
    case "mul":
      return <rect x={1} y={1} width={w - 2} height={h - 2} rx={(h - 2) / 2} {...s} />;
    case "port":
      return <polygon points={`1,1 ${w - 9},1 ${w - 1},${h / 2} ${w - 9},${h - 1} 1,${h - 1}`} {...s} />;
    case "reg":
      return (
        <>
          <rect x={1} y={1} width={w - 2} height={h - 2} {...s} />
          <polyline points={`1,${h - 13} 8,${h - 7} 1,${h - 1}`} {...s} />
        </>
      );
    case "submodule":
      return <rect x={1} y={1} width={w - 2} height={h - 2} rx={3} strokeDasharray="4 3" {...s} />;
    default:
      return <rect x={1} y={1} width={w - 2} height={h - 2} rx={7} {...s} />;
  }
}

const Part = memo(function Part({ data }: NodeProps<PartNode>) {
  return (
    <div className="c-part" data-kind={data.kind} data-agent={data.agent ? "" : undefined} data-picked={data.picked ? "" : undefined} style={{ width: data.w, height: data.h }}>
      <svg className="c-shape" width={data.w} height={data.h} aria-hidden>
        <Outline kind={data.kind} w={data.w} h={data.h} />
      </svg>
      <Handle id="in" type="target" position={Position.Top} isConnectable={false} />
      <Handle id="back-in-r" type="target" position={Position.Right} isConnectable={false} />
      <Handle id="back-in-l" type="target" position={Position.Left} isConnectable={false} />
      <span className="c-k">{data.title}</span>
      <span className="c-w m">{data.bits}</span>
      <Handle id="out" type="source" position={Position.Bottom} isConnectable={false} />
      <Handle id="back-out-r" type="source" position={Position.Right} isConnectable={false} />
      <Handle id="back-out-l" type="source" position={Position.Left} isConnectable={false} />
    </div>
  );
});

const NODE_TYPES = { part: Part };

/** The side a feedback wire loops round: the one with fewer other parts inside the loop's box. The right side wins a tie. */
function loopSide(at: (id: string) => { x: number; y: number }, c: Circuit, from: string, to: string): "r" | "l" {
  const kinds = new Map(c.nodes.map((n) => [n.id, n.kind]));
  const box = (id: string) => {
    const p = at(id);
    const { w, h } = SIZE[kinds.get(id) ?? "logic"];
    return { l: p.x - w / 2, r: p.x + w / 2, t: p.y - h / 2, b: p.y + h / 2 };
  };
  const a = box(from);
  const b = box(to);
  const top = Math.min(a.t, b.t);
  const bottom = Math.max(a.b, b.b);
  const inside = (side: "r" | "l") =>
    c.nodes.filter((n) => {
      if (n.id === from || n.id === to) return false;
      const o = box(n.id);
      const clear = side === "r" ? o.r > Math.min(a.r, b.r) : o.l < Math.max(a.l, b.l);
      return clear && o.b > top && o.t < bottom;
    }).length;
  return inside("l") < inside("r") ? "l" : "r";
}

/**
 * Top to bottom with dagre: inputs at the top, outputs at the bottom, so the picture grows down the narrow panel rather
 * than across it. Same input, same picture.
 */
function layout(c: Circuit, agent: ReadonlySet<string>, picked: string | null, big: boolean): { nodes: PartNode[]; edges: Edge[] } {
  const dirs = new Map(c.ports.map((p) => [p.name, p.dir]));
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 16, ranksep: 34, marginx: 10, marginy: 10 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of c.nodes) g.setNode(n.id, { width: SIZE[n.kind].w, height: SIZE[n.kind].h });
  for (const e of c.edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  const nodes: PartNode[] = c.nodes.map((n) => {
    const p = g.node(n.id);
    const { w, h } = SIZE[n.kind];
    return {
      id: n.id,
      type: "part",
      position: { x: p.x - w / 2, y: p.y - h / 2 },
      width: w,
      height: h,
      draggable: false,
      connectable: false,
      ariaLabel: `${titleOf(n, dirs.get(n.label))}, ${n.width} ${n.width === 1 ? "bit" : "bits"}`,
      data: {
        kind: n.kind,
        title: titleOf(n, dirs.get(n.label)),
        bits: `${n.width} ${n.width === 1 ? "bit" : "bits"}`,
        agent: agent.has(n.id),
        picked: picked === n.id,
        w,
        h,
      },
    };
  });
  const edges: Edge[] = c.edges.map((e, i) => {
    const lit = agent.has(e.source) && agent.has(e.target);
    // A wire that feeds back to a part at or above its source (a register's next value) would run up behind the parts from
    // the bottom of one to the top of the other. It leaves and enters on one side instead and loops round the parts.
    const back = e.source === e.target || g.node(e.target).y <= g.node(e.source).y;
    const side = back ? loopSide((id) => g.node(id), c, e.source, e.target) : "r";
    return {
      id: `e${i}-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      sourceHandle: back ? `back-out-${side}` : "out",
      targetHandle: back ? `back-in-${side}` : "in",
      ...(back ? { type: "smoothstep", pathOptions: { offset: 26, borderRadius: 8 } } : {}),
      selectable: false,
      focusable: false,
      className: `c-wire${e.width > 1 ? " c-bus" : ""}${lit ? " c-lit" : ""}`,
      ...(big && e.width > 1 ? { label: `${e.width}b`, labelBgPadding: [3, 1] as [number, number], labelBgBorderRadius: 3 } : {}),
    };
  });
  return { nodes, edges };
}

/** The smallest zoom the first view opens at, so the words stay readable; Fit still shows the whole circuit. */
const READABLE = { panel: 0.85, big: 0.6 };

function Zoom({ fitIds, reduced, big, frame }: { fitIds: string[]; reduced: boolean; big: boolean; frame: RefObject<HTMLDivElement | null> }) {
  const rf = useReactFlow();
  const key = fitIds.join(",");
  // The camera follows the walkthrough to its parts. Without parts it opens on the inputs at a readable size, centred
  // across, and the reader pans down (or presses Fit for the whole circuit).
  useEffect(() => {
    const ids = key ? key.split(",") : [];
    const t = window.setTimeout(() => {
      const duration = reduced ? 0 : 300;
      if (ids.length > 0) {
        void rf.fitView({ nodes: ids.map((id) => ({ id })), padding: 0.25, maxZoom: 1.3, duration });
        return;
      }
      const el = frame.current;
      const b = rf.getNodesBounds(rf.getNodes());
      if (!el || b.width === 0) return;
      const w = el.clientWidth;
      const h = el.clientHeight;
      const zoom = Math.min(1.2, Math.max(big ? READABLE.big : READABLE.panel, Math.min(w / b.width, h / b.height) * 0.94));
      const x = b.width * zoom < w ? (w - b.width * zoom) / 2 - b.x * zoom : 12 - b.x * zoom + (w - 24 - b.width * zoom) / 2;
      void rf.setViewport({ x, y: 12 - b.y * zoom, zoom }, { duration });
    }, 30);
    return () => window.clearTimeout(t);
  }, [key, rf, reduced, big, frame]);
  return (
    <div className="c-zoom" role="group" aria-label="Zoom">
      <button type="button" className="b-ib" aria-label="Zoom in" onClick={() => void rf.zoomIn({ duration: reduced ? 0 : 150 })}>
        <Plus aria-hidden />
      </button>
      <button type="button" className="b-ib" aria-label="Zoom out" onClick={() => void rf.zoomOut({ duration: reduced ? 0 : 150 })}>
        <Minus aria-hidden />
      </button>
      <button type="button" className="b-ib" aria-label="Fit the whole circuit" onClick={() => void rf.fitView({ padding: 0.08, duration: reduced ? 0 : 200 })}>
        <Maximize aria-hidden />
      </button>
    </div>
  );
}

/** One part in words, with what feeds it and what it feeds: the same facts as the picture, for reading or a screen reader. */
function partWords(c: Circuit, id: string): string | null {
  const n = c.nodes.find((x) => x.id === id);
  if (!n) return null;
  const dirs = new Map(c.ports.map((p) => [p.name, p.dir]));
  const name = (nid: string) => {
    const m = c.nodes.find((x) => x.id === nid);
    return m ? `${titleOf(m, dirs.get(m.label))} (${m.width} ${m.width === 1 ? "bit" : "bits"})` : nid;
  };
  const from = c.edges.filter((e) => e.target === id).map((e) => name(e.source));
  const to = c.edges.filter((e) => e.source === id).map((e) => name(e.target));
  return `${titleOf(n, dirs.get(n.label))}, ${n.width} ${n.width === 1 ? "bit" : "bits"}. ${from.length ? `Takes from: ${from.join(", ")}.` : "Takes from nothing drawn."} ${to.length ? `Feeds: ${to.join(", ")}.` : "Feeds nothing drawn."}`;
}

/**
 * The circuit of one version as Yosys reads its Verilog (before it maps to cells): registers, adders, multipliers, muxes,
 * compares, logic and ports, each with its width. `agent` is the walkthrough's highlight (dashed outline); a click is the
 * reader's own pick (solid outline). Neither moves the other. Pan with a drag, zoom with the buttons or a pinch; the
 * first view opens readable on the inputs rather than shrinking a big circuit to fit a narrow panel.
 */
export function CircuitView({ circuit, agent, label, big }: { circuit: Circuit; agent: string[]; label: string; big: boolean }) {
  const reduced = useReducedMotion();
  const [picked, setPicked] = useState<{ key: string; id: string } | null>(null);
  const pickedId = picked?.key === label ? picked.id : null;
  const agentSet = useMemo(() => new Set(agent), [agent]);
  const { nodes, edges } = useMemo(() => layout(circuit, agentSet, pickedId, big), [circuit, agentSet, pickedId, big]);
  const fitIds = useMemo(() => agent.filter((id) => circuit.nodes.some((n) => n.id === id)), [agent, circuit]);
  const words = pickedId ? partWords(circuit, pickedId) : null;
  const frame = useRef<HTMLDivElement>(null);

  return (
    <div className="c-wrap" data-big={big ? "" : undefined}>
      <div className="c-flow" role="group" aria-label={label} ref={frame}>
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            nodesFocusable
            edgesFocusable={false}
            onNodeClick={(_, n) => setPicked((p) => (p?.key === label && p.id === n.id ? null : { key: label, id: n.id }))}
            minZoom={0.15}
            maxZoom={2.5}
            zoomOnScroll={big}
            preventScrolling={big}
            zoomOnDoubleClick={false}
            attributionPosition="bottom-left"
          >
            <Zoom fitIds={fitIds} reduced={reduced} big={big} frame={frame} />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
      <p className="b-note c-pick" aria-live="polite">
        {words ?? "Pick a part to see what feeds it and what it feeds."}
      </p>
    </div>
  );
}
