import type { Candidate } from "@/lib/contracts/types";
import { isContentNode, ZONE_IDS, type VerityEdge, type VerityNode } from "./zones";

/** At most this many content nodes are visible (ARCHITECTURE 6 rule 5). Zone frames do not count. */
export const MAX_CONTENT_NODES = 12;

export type VisibilityContext = {
  candidates: Record<string, Candidate>;
  baselineId: string | null;
  winnerId: string | null;
  round: number;
};

/**
 * Hides rejected versions (oldest first), then non-elite measured versions from earlier rounds
 * (oldest first) until the budget holds. Stateless: hidden flags are recomputed from scratch each call,
 * so replay and live always agree. The baseline, the winner and elites are never hidden.
 */
export function applyVisibilityBudget(
  nodes: VerityNode[],
  edges: VerityEdge[],
  ctx: VisibilityContext,
): { nodes: VerityNode[]; edges: VerityEdge[]; hiddenCount: number } {
  const content = nodes.filter(isContentNode);
  let over = content.length - MAX_CONTENT_NODES;
  const hide = new Set<string>();

  if (over > 0) {
    const cands = content
      .filter((n) => n.data.kind === "candidate")
      .map((n) => ({ node: n, cand: ctx.candidates[n.data.refId] }))
      .filter((x): x is { node: VerityNode; cand: Candidate } => Boolean(x.cand));
    const isProtected = (c: Candidate) => c.id === ctx.baselineId || c.id === ctx.winnerId || c.isElite || c.isBaseline;
    for (const { node, cand } of cands) {
      if (over <= 0) break;
      if (cand.status === "rejected" && !isProtected(cand)) {
        hide.add(node.id);
        over--;
      }
    }
    for (const { node, cand } of cands) {
      if (over <= 0) break;
      if (!hide.has(node.id) && cand.status === "measured" && cand.round < ctx.round && !isProtected(cand)) {
        hide.add(node.id);
        over--;
      }
    }
  }

  let changed = false;
  const nextNodes = nodes.map((n) => {
    if (n.data.kind === "zone") return n;
    const hidden = hide.has(n.id);
    if (Boolean(n.hidden) === hidden) return n;
    changed = true;
    return { ...n, hidden };
  });
  const hiddenIds = new Set(nextNodes.filter((n) => n.hidden).map((n) => n.id));
  const nextEdges = edges.map((e) => {
    const hidden = hiddenIds.has(e.source) || hiddenIds.has(e.target);
    if (Boolean(e.hidden) === hidden) return e;
    changed = true;
    return { ...e, hidden };
  });

  // The tree zone header reports the hidden count.
  const note = hide.size > 0 ? `+${hide.size} hidden versions (see the list)` : undefined;
  const finalNodes = nextNodes.map((n) => {
    if (n.id !== ZONE_IDS.tree || n.data.note === note) return n;
    changed = true;
    return { ...n, data: { ...n.data, note } };
  });

  return changed
    ? { nodes: finalNodes, edges: nextEdges, hiddenCount: hide.size }
    : { nodes, edges, hiddenCount: hide.size };
}
