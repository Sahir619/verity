import dagre from "@dagrejs/dagre";
import { isContentNode, NODE_SIZE, WORLD, ZONE_IDS, type VerityEdge, type VerityNode } from "./zones";

function place(n: VerityNode, x: number, y: number): VerityNode {
  if (n.position.x === x && n.position.y === y) return n;
  return { ...n, position: { x, y } };
}

function resizeFrame(n: VerityNode, width: number, height: number): VerityNode {
  if (n.width === width && n.height === height) return n;
  return { ...n, width, height };
}

/**
 * Plan zone: a fixed column (spec, blocks in arrival order, golden model). Slots are reserved for every
 * visible node, but nodes the user has moved keep their own position.
 */
export function layoutPlan(nodes: VerityNode[]): VerityNode[] {
  const planNodes = nodes.filter((n) => n.data.zone === "plan" && isContentNode(n) && !n.hidden);
  const rank = (n: VerityNode) => (n.data.kind === "spec" ? 0 : n.data.kind === "golden" ? 2 : 1);
  const ordered = planNodes
    .map((n, i) => ({ n, i }))
    .sort((a, b) => rank(a.n) - rank(b.n) || a.i - b.i)
    .map((x) => x.n);

  const slots = new Map<string, { x: number; y: number }>();
  let y = WORLD.headerH;
  for (const n of ordered) {
    slots.set(n.id, { x: WORLD.planPad, y });
    const h = n.height ?? NODE_SIZE[n.data.kind as keyof typeof NODE_SIZE]?.height ?? 72;
    y += h + WORLD.planGap;
  }
  const frameH = Math.max(WORLD.minFrameH, y - WORLD.planGap + WORLD.planPad);

  return nodes.map((n) => {
    if (n.id === ZONE_IDS.plan) return resizeFrame(n, WORLD.planFrameW, frameH);
    const slot = slots.get(n.id);
    if (!slot || n.data.origin === "user") return n;
    return place(n, slot.x, slot.y);
  });
}

/**
 * Tree zone: dagre top-to-bottom over visible candidate nodes and lineage edges. The root (first version) is
 * anchored at the horizontal center of the minimum frame so existing versions do not drift as the tree grows.
 * Positions are deterministic: replay and live draw the same picture.
 */
export function layoutTree(nodes: VerityNode[], edges: VerityEdge[]): VerityNode[] {
  const cands = nodes.filter((n) => n.data.zone === "tree" && n.data.kind === "candidate" && !n.hidden);
  const ids = new Set(cands.map((n) => n.id));

  let offsets = new Map<string, { x: number; y: number }>();
  let right = WORLD.treeX + WORLD.treeMinW;
  let bottom: number = WORLD.minFrameH;

  if (cands.length > 0) {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "TB", nodesep: 24, ranksep: 56 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const n of cands) g.setNode(n.id, { width: NODE_SIZE.candidate.width, height: NODE_SIZE.candidate.height });
    for (const e of edges) {
      const kind = (e.data as { kind?: string } | undefined)?.kind;
      if (kind === "lineage" && ids.has(e.source) && ids.has(e.target)) g.setEdge(e.source, e.target);
    }
    dagre.layout(g);

    const root = cands[0];
    const rootPos = g.node(root.id);
    const anchorX = WORLD.treeX + WORLD.treeMinW / 2;
    let dx = anchorX - rootPos.x;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const n of cands) {
      const p = g.node(n.id);
      minX = Math.min(minX, p.x - p.width / 2);
      maxX = Math.max(maxX, p.x + p.width / 2);
      minY = Math.min(minY, p.y - p.height / 2);
      maxY = Math.max(maxY, p.y + p.height / 2);
    }
    const deficit = WORLD.treeX + WORLD.treePad - (minX + dx);
    if (deficit > 0) dx += deficit;
    const dy = WORLD.headerH - minY;
    offsets = new Map(
      cands.map((n) => {
        const p = g.node(n.id);
        return [n.id, { x: Math.round(p.x - p.width / 2 + dx), y: Math.round(p.y - p.height / 2 + dy) }] as const;
      }),
    );
    right = Math.max(right, Math.round(maxX + dx) + WORLD.treePad);
    bottom = Math.max(bottom, Math.round(maxY + dy) + WORLD.treePad);
  }

  return nodes.map((n) => {
    if (n.id === ZONE_IDS.tree) return resizeFrame(n, right - WORLD.treeX, bottom);
    const pos = offsets.get(n.id);
    if (!pos || n.data.origin === "user") return n;
    return place(n, pos.x, pos.y);
  });
}

/** Both zones, in the order the reducer needs. */
export function layoutAll(nodes: VerityNode[], edges: VerityEdge[]): VerityNode[] {
  return layoutTree(layoutPlan(nodes), edges);
}

/**
 * The proof edge (winner to v1) leaves the winner's bottom, runs under the tree, up its right margin and into v1
 * from the right. The two coordinates are derived from the laid-out tree so the line clears every version and stays
 * correct as the tree grows.
 */
export function routeProofEdges(nodes: VerityNode[], edges: VerityEdge[]): VerityEdge[] {
  const cands = nodes.filter((n) => n.data.kind === "candidate" && !n.hidden);
  if (cands.length === 0) return edges;
  const treeRight = Math.max(...cands.map((n) => n.position.x + (n.width ?? NODE_SIZE.candidate.width)));
  const treeBottom = Math.max(...cands.map((n) => n.position.y + (n.height ?? NODE_SIZE.candidate.height)));
  const xr = Math.round(treeRight + 12);
  const yb = Math.round(treeBottom + 16);
  let changed = false;
  const next = edges.map((e) => {
    const data = e.data as { kind?: string; xr?: number; yb?: number } | undefined;
    if (data?.kind !== "proof") return e;
    if (data.xr === xr && data.yb === yb) return e;
    changed = true;
    return { ...e, data: { ...data, xr, yb } };
  });
  return changed ? next : edges;
}
