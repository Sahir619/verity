import type {
  Candidate,
  CanvasOp,
  RunEvent,
  RunMode,
  RunState,
  StageId,
} from "@/lib/contracts/types";
import { layoutAll, routeProofEdges } from "@/lib/canvas/layout";
import { applyVisibilityBudget } from "@/lib/canvas/visibility";
import {
  edgeFor,
  isContentNode,
  makeZoneNodes,
  sizeFor,
  type VerityEdge,
  type VerityNode,
} from "@/lib/canvas/zones";

export interface RunSnapshot {
  run: RunState;
  nodes: VerityNode[];
  edges: VerityEdge[];
}

const STAGE_IDS: StageId[] = ["spec", "plan", "lock", "rtl", "lint", "sim", "fix", "synth", "evolve", "prove"];

/** Candidate statuses during which the lineage edge into the version animates. */
const BUSY_STATUSES = new Set(["drafting", "linting", "simulating", "fixing", "synthesizing"]);

export function initialSnapshot(init: { runId: string; mode: RunMode; sample: boolean; title: string }): RunSnapshot {
  const stages = Object.fromEntries(STAGE_IDS.map((id) => [id, { status: "pending" as const }])) as RunState["stages"];
  const candidates: Record<string, Candidate> = {};
  return {
    run: {
      runId: init.runId,
      mode: init.mode,
      sample: init.sample,
      title: init.title,
      phase: "idle",
      spec: null,
      plan: { revision: 0, blocks: [], lastDiff: null },
      stages,
      candidates,
      candidateOrder: [],
      round: 0,
      elites: [],
      baselineId: null,
      winnerId: null,
      aiWinnerId: null,
      winnerBy: null,
      gate: { state: "closed", autoContinueMs: 0, openedAtMs: 0 },
      humanInputs: [],
      credits: { usedUsd: 0, capUsd: null },
      caption: null,
      artifacts: [],
      lastSeq: 0,
      tMs: 0,
    },
    nodes: makeZoneNodes(),
    edges: [],
  };
}

/** Re-runs the visibility budget and both layouts. Called after any topology or visibility change. */
function relayout(s: RunSnapshot): RunSnapshot {
  const vis = applyVisibilityBudget(s.nodes, s.edges, {
    candidates: s.run.candidates,
    baselineId: s.run.baselineId,
    winnerId: s.run.winnerId,
    round: s.run.round,
  });
  const laidOut = layoutAll(vis.nodes, vis.edges);
  // Keep array identity when nothing moved so React Flow does not re-render on every event.
  const edgesRouted = routeProofEdges(laidOut, vis.edges);
  const nodes = laidOut.length === s.nodes.length && laidOut.every((n, i) => n === s.nodes[i]) ? s.nodes : laidOut;
  if (nodes === s.nodes && edgesRouted === s.edges) return s;
  return { run: s.run, nodes, edges: edgesRouted };
}

function defaultNodeStatus(kind: string, candidates: Record<string, Candidate>, refId: string): string {
  switch (kind) {
    case "block":
      return "drafted";
    case "golden":
      return "pending";
    case "candidate":
      return candidates[refId]?.status ?? "drafting";
    default:
      return "placed";
  }
}

function applyOp(s: RunSnapshot, by: "ai" | "user" | "system", op: CanvasOp, tMs: number): RunSnapshot {
  switch (op.op) {
    case "add_node": {
      if (s.nodes.some((n) => n.id === op.id)) return s;
      const refId = op.refId ?? op.id;
      const size = sizeFor(op.kind);
      const isNewBlock = op.kind === "block" && s.run.plan.revision > 1;
      const node: VerityNode = {
        id: op.id,
        type: op.kind,
        position: { x: 0, y: 0 },
        width: size.width,
        height: size.height,
        draggable: op.kind === "block",
        selectable: true,
        deletable: false,
        connectable: false,
        data: {
          kind: op.kind,
          refId,
          label: op.label,
          zone: op.zone,
          status: defaultNodeStatus(op.kind, s.run.candidates, refId),
          origin: "ai",
          ...(isNewBlock ? { diff: "new" as const, diffAtMs: tMs } : {}),
        },
      };
      return relayout({ ...s, nodes: [...s.nodes, node] });
    }
    case "connect": {
      const id = `${op.source}->${op.target}`;
      const hasEnds = s.nodes.some((n) => n.id === op.source) && s.nodes.some((n) => n.id === op.target);
      if (!hasEnds || s.edges.some((e) => e.id === id)) return s;
      const edge = edgeFor(op.source, op.target, op.kind, op.label);
      const target = s.nodes.find((n) => n.id === op.target);
      const animated = op.kind === "lineage" && target ? BUSY_STATUSES.has(target.data.status) : false;
      return relayout({ ...s, edges: [...s.edges, animated ? { ...edge, animated } : edge] });
    }
    case "move_node": {
      const node = s.nodes.find((n) => n.id === op.id);
      if (!node || !isContentNode(node)) return s;
      // Once a user moves a node, the AI may update its data but never its position.
      if (by === "ai" && node.data.origin === "user") return s;
      const moved: VerityNode =
        by === "user"
          ? { ...node, position: { x: op.x, y: op.y }, data: { ...node.data, origin: "user" } }
          : { ...node, position: { x: op.x, y: op.y } };
      return { ...s, nodes: s.nodes.map((n) => (n.id === op.id ? moved : n)) };
    }
    case "update_status": {
      const node = s.nodes.find((n) => n.id === op.id);
      if (!node) return s;
      if (node.data.status === op.status && node.data.note === op.note) return s;
      const next: VerityNode = { ...node, data: { ...node.data, status: op.status, note: op.note } };
      return { ...s, nodes: s.nodes.map((n) => (n.id === op.id ? next : n)) };
    }
    case "remove": {
      if (!s.nodes.some((n) => n.id === op.id)) return s;
      return relayout({
        ...s,
        nodes: s.nodes.filter((n) => n.id !== op.id),
        edges: s.edges.filter((e) => e.source !== op.id && e.target !== op.id),
      });
    }
  }
}

/** Keeps node status and the animated lineage edges in step with candidate records. */
function syncCandidateNodes(s: RunSnapshot, c: Candidate): RunSnapshot {
  let changed = false;
  const nodes = s.nodes.map((n) => {
    if (n.data.kind !== "candidate" || n.data.refId !== c.id || n.data.status === c.status) return n;
    changed = true;
    return { ...n, data: { ...n.data, status: c.status } };
  });
  const busy = BUSY_STATUSES.has(c.status);
  const edges = s.edges.map((e) => {
    const kind = (e.data as { kind?: string } | undefined)?.kind;
    if (kind !== "lineage" || e.target !== c.id || Boolean(e.animated) === busy) return e;
    changed = true;
    return { ...e, animated: busy };
  });
  return changed ? { ...s, nodes, edges } : s;
}

function applyPlan(s: RunSnapshot, e: Extract<RunEvent, { t: "plan" }>): RunSnapshot {
  const changed = new Set(e.diff?.changed ?? []);
  const added = new Set(e.diff?.added ?? []);
  const blocksById = new Map(e.blocks.map((b) => [b.id, b]));
  const nodes = s.nodes.map((n) => {
    if (n.data.kind !== "block") return n;
    const block = blocksById.get(n.data.refId);
    let diff: "new" | "changed" | undefined;
    let diffAtMs: number | undefined;
    if (changed.has(n.data.refId)) {
      diff = "changed";
      diffAtMs = e.tMs;
    } else if (added.has(n.data.refId)) {
      diff = "new";
      diffAtMs = e.tMs;
    }
    const label = block ? block.name : n.data.label;
    if (n.data.diff === diff && n.data.diffAtMs === diffAtMs && n.data.label === label) return n;
    return { ...n, data: { ...n.data, label, diff, diffAtMs } };
  });
  return {
    run: {
      ...s.run,
      plan: { revision: e.revision, blocks: e.blocks, lastDiff: e.diff ?? null, reason: e.reason },
    },
    nodes,
    edges: s.edges,
  };
}

function mergeArtifact(list: RunState["artifacts"], path: string, content: string): RunState["artifacts"] {
  const i = list.findIndex((a) => a.path === path);
  if (i < 0) return [...list, { path, content }];
  const next = list.slice();
  next[i] = { path, content };
  return next;
}

/**
 * The only function that turns events into state (ARCHITECTURE 6). Pure and deterministic: no timers, no I/O,
 * no seq check (drivers dedupe live events; replay applies in order).
 */
export function reduce(s: RunSnapshot, e: RunEvent): RunSnapshot {
  let next: RunSnapshot = s;
  const bump = (run: RunState): RunState => ({ ...run, lastSeq: Math.max(run.lastSeq, e.seq), tMs: e.tMs });

  switch (e.t) {
    case "run":
      next = { ...s, run: { ...s.run, phase: e.phase } };
      break;
    case "stage":
      next = {
        ...s,
        run: {
          ...s.run,
          stages: { ...s.run.stages, [e.stage]: { status: e.status, detail: e.detail, round: e.round } },
        },
      };
      break;
    case "op":
      next = applyOp(s, e.by, e.op, e.tMs);
      break;
    case "spec":
      next = { ...s, run: { ...s.run, spec: e.spec, title: e.spec.title } };
      break;
    case "plan":
      next = applyPlan(s, e);
      break;
    case "gate":
      next = {
        ...s,
        run: { ...s.run, gate: { state: e.state, autoContinueMs: e.autoContinueMs, openedAtMs: e.tMs } },
      };
      break;
    case "candidate": {
      const c = e.candidate;
      const known = c.id in s.run.candidates;
      const run: RunState = {
        ...s.run,
        candidates: { ...s.run.candidates, [c.id]: c },
        candidateOrder: known ? s.run.candidateOrder : [...s.run.candidateOrder, c.id],
        baselineId: c.isBaseline ? c.id : s.run.baselineId,
      };
      next = relayout(syncCandidateNodes({ ...s, run }, c));
      break;
    }
    case "round":
      next = relayout({
        ...s,
        run: { ...s.run, round: Math.max(s.run.round, e.round), elites: e.elites ?? s.run.elites },
      });
      break;
    case "winner":
      next = relayout({
        ...s,
        run: { ...s.run, winnerId: e.candidateId, aiWinnerId: e.aiPickId, winnerBy: e.by },
      });
      break;
    case "human":
      next = { ...s, run: { ...s.run, humanInputs: [...s.run.humanInputs, e.input] } };
      break;
    case "caption":
      next = { ...s, run: { ...s.run, caption: { text: e.text, chapter: e.chapter, tMs: e.tMs } } };
      break;
    case "credits":
      next = { ...s, run: { ...s.run, credits: { usedUsd: e.usedUsd, capUsd: e.capUsd } } };
      break;
    case "artifact":
      next = { ...s, run: { ...s.run, artifacts: mergeArtifact(s.run.artifacts, e.path, e.content) } };
      break;
  }

  return { ...next, run: bump(next.run) };
}
