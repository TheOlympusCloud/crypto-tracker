import type { Graph } from "./types";

/**
 * Folding a busy address (an exchange, a mixer, a DEX) hides the counterparties that deal
 * with nothing but folded addresses, so it reads as one node. Only the picture changes:
 * verdicts and origins are worked out on the server from everything. My wallets, the folded
 * addresses themselves and the addresses in `keep` (a shown path) are never hidden. A folded
 * node carries `folded`: how many counterparties it hides. Guesses below `minConf` are not
 * drawn, so they tie nothing.
 */
export function fold(graph: Graph, folded: ReadonlySet<string>, keep: readonly string[] = [], minConf = 0): Graph {
  if (!folded.size) return graph;
  const near = new Map<string, Set<string>>(); // address -> its neighbours, through transfers and guesses alike
  const tie = (a: string, b: string) => {
    if (a === b) return; // sending to itself ties an address to nothing
    if (!near.has(a)) near.set(a, new Set());
    near.get(a)!.add(b);
  };
  for (const e of graph.edges) {
    tie(e.src, e.dst);
    tie(e.dst, e.src);
  }
  for (const k of graph.links) {
    if (k.confidence >= minConf) {
      tie(k.a, k.b);
      tie(k.b, k.a);
    }
  }
  const stay = new Set(keep);
  const hidden = new Set<string>();
  for (const n of graph.nodes) {
    const around = [...(near.get(n.address) ?? [])];
    if (!n.mine && !folded.has(n.address) && !stay.has(n.address) && around.length && around.every((a) => folded.has(a))) hidden.add(n.address);
  }
  const count = new Map<string, number>();
  for (const a of hidden) for (const b of near.get(a) ?? []) count.set(b, (count.get(b) ?? 0) + 1);
  return {
    nodes: graph.nodes.filter((n) => !hidden.has(n.address)).map((n) => (folded.has(n.address) ? { ...n, folded: count.get(n.address) ?? 0 } : n)),
    edges: graph.edges.filter((e) => !hidden.has(e.src) && !hidden.has(e.dst)),
    links: graph.links.filter((k) => !hidden.has(k.a) && !hidden.has(k.b)),
  };
}
