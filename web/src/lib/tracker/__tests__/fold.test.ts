import { describe, it, expect } from "vitest";
import { fold } from "../fold";
import type { Graph, GraphEdge } from "../types";

const [ME, CEX, MIXER, S1, S2, S3, T] = ["me", "cex", "mixer", "s1", "s2", "s3", "t"] as const;
const edge = (src: string, dst: string) => ({ src, dst }) as GraphEdge;

// My wallet, an exchange with strangers that only deal with it (s1, s2), one that also paid
// me (s3), a stranger who only dealt with me (t), and a stranger guessed to share an owner
// with s2 through a mixer.
const GRAPH: Graph = {
  nodes: [ME, CEX, MIXER, S1, S2, S3, T].map((a) => ({ address: a, mine: Number(a === ME), public: 0, name: "" })),
  edges: [edge(S1, CEX), edge(CEX, S2), edge(S3, CEX), edge(S3, ME), edge(T, ME)],
  links: [{ a: S2, b: MIXER, kind: "common-input", confidence: 0.9, count: 1 }],
};
const shown = (g: Graph) => Object.fromEntries(g.nodes.map((n) => [n.address, n.folded]));

describe("fold", () => {
  it("changes nothing when nothing is folded", () => {
    expect(fold(GRAPH, new Set())).toBe(GRAPH);
  });

  it("hides the strangers that only deal with a folded address", () => {
    const g = fold(GRAPH, new Set([CEX]));
    // s1 only dealt with the exchange; s2 is also tied to the mixer; s3 also paid me.
    expect(shown(g)).toEqual({ me: undefined, cex: 1, mixer: undefined, s2: undefined, s3: undefined, t: undefined });
    expect(g.edges).not.toContainEqual(edge(S1, CEX));
    expect(g.edges).toContainEqual(edge(CEX, S2));
  });

  it("hides strangers between folded addresses and counts them on both", () => {
    const g = fold(GRAPH, new Set([CEX, MIXER]));
    expect(shown(g)).toEqual({ me: undefined, cex: 2, mixer: 1, s3: undefined, t: undefined });
    expect(g.links).toEqual([]);
    expect(g.edges.every((e) => e.src !== S2 && e.dst !== S2)).toBe(true);
  });

  it("never hides my wallets or a shown path", () => {
    const g = fold(GRAPH, new Set([ME])); // my own busy wallet folds too, but is never hidden itself
    expect(shown(g)).toEqual({ me: 1, cex: undefined, mixer: undefined, s1: undefined, s2: undefined, s3: undefined });
    expect(S1 in shown(fold(GRAPH, new Set([CEX]), [S1, CEX]))).toBe(true); // a highlighted path stays whole
  });

  it("ties a stranger to an address only through what is drawn", () => {
    // The slider hides s2's guess (0.9) to the mixer: s2 deals with the exchange alone.
    expect(shown(fold(GRAPH, new Set([CEX]), [], 0.95))).toEqual({ me: undefined, cex: 2, mixer: undefined, s3: undefined, t: undefined });
    // Sending to itself does not tie an address to anything.
    const looped = { ...GRAPH, edges: [...GRAPH.edges, edge(S1, S1)] };
    expect(S1 in shown(fold(looped, new Set([CEX])))).toBe(false);
  });
});
