import type { Edge, Node } from "@xyflow/react";
import type { EdgeKind, NodeData, NodeKind } from "@/lib/contracts/types";

export type VerityNode = Node<NodeData>;
export type VerityEdge = Edge;

/** Fixed node sizes (ARCHITECTURE 6 rule 4). Zone frames are sized from layout output. */
export const NODE_SIZE = {
  spec: { width: 240, height: 112 },
  block: { width: 240, height: 72 },
  golden: { width: 240, height: 88 },
  candidate: { width: 192, height: 104 },
} as const;

/** World geometry. All values are canvas units. */
export const WORLD = {
  planX: 0,
  planFrameW: 288,
  planPad: 24,
  headerH: 72,
  planGap: 20,
  treeX: 336,
  treeMinW: 672,
  treePad: 24,
  /** Reserved so the whole world is visible from the first frame and fitView stays calm as it fills. */
  minFrameH: 684,
} as const;

export const ZONE_IDS = { plan: "zone-plan", tree: "zone-tree" } as const;

export function isContentNode(n: VerityNode): boolean {
  return n.data.kind !== "zone";
}

/** The two zone frames. They exist from the first snapshot so the canvas is never blank. */
export function makeZoneNodes(): VerityNode[] {
  const base = {
    selectable: false,
    draggable: false,
    focusable: false,
    deletable: false,
    connectable: false,
    zIndex: -1,
  } as const;
  return [
    {
      ...base,
      id: ZONE_IDS.plan,
      type: "zone",
      position: { x: WORLD.planX, y: 0 },
      width: WORLD.planFrameW,
      height: WORLD.minFrameH,
      data: { kind: "zone", refId: "plan", label: "Plan Board", zone: "plan", status: "static", origin: "ai" },
    },
    {
      ...base,
      id: ZONE_IDS.tree,
      type: "zone",
      position: { x: WORLD.treeX, y: 0 },
      width: WORLD.treeMinW,
      height: WORLD.minFrameH,
      data: { kind: "zone", refId: "tree", label: "Evolution Tree", zone: "tree", status: "static", origin: "ai" },
    },
  ];
}

export function sizeFor(kind: NodeKind): { width: number; height: number } {
  return NODE_SIZE[kind];
}

/** Edge styling. Colors are token variables, never raw values. */
export function edgeFor(source: string, target: string, kind: EdgeKind, label?: string): VerityEdge {
  const common = {
    id: `${source}->${target}`,
    source,
    target,
    type: "smoothstep" as const,
    focusable: false,
    selectable: false,
    deletable: false,
    data: { kind },
  };
  const labelProps = label
    ? {
        label,
        labelStyle: { fill: "var(--fg-muted)", fontSize: 12 },
        labelBgStyle: { fill: "var(--background)" },
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 3,
      }
    : {};
  switch (kind) {
    case "plan":
    case "lineage":
      return {
        ...common,
        sourceHandle: "out-bottom",
        targetHandle: "in-top",
        style: { stroke: "var(--edge)", strokeWidth: 1 },
      };
    case "checks":
      return {
        ...common,
        sourceHandle: "out-right",
        targetHandle: "in-left",
        style: { stroke: "var(--edge)", strokeWidth: 1, strokeDasharray: "1 5", strokeLinecap: "round" },
        ...labelProps,
      };
    case "proof":
      // Drawn by components/canvas/edges/ProofEdge.tsx; data.xr and data.yb are set by the reducer after layout.
      return {
        ...common,
        type: "proof",
        sourceHandle: "out-bottom",
        targetHandle: "in-right",
        style: { stroke: "var(--edge-proof)", strokeWidth: 1.5, strokeDasharray: "6 4" },
        ...labelProps,
      };
  }
}
