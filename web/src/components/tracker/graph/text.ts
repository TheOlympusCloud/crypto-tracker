import { day, num } from "@/lib/tracker/format";
import { pct } from "@/lib/tracker/kinds";
import type { Graph, GraphNode } from "@/lib/tracker/types";

type T = (key: string, options?: Record<string, unknown>) => string;

/** One hop of a shown path, spelled out: every transfer between the two addresses, then every guess. */
export function hopTexts(graph: Graph, a: string, b: string, t: T, lang: string): string[] {
  const facts = graph.edges.filter((e) => (e.src === a && e.dst === b) || (e.src === b && e.dst === a));
  const guesses = graph.links.filter((k) => (k.a === a && k.b === b) || (k.a === b && k.b === a));
  return [
    ...facts.map((e) => {
      const what = {
        amount: num(e.amount, lang),
        asset: e.asset,
        chain: e.chain,
        transfers: t("tracker-graph:hop.transfers", { count: e.count }),
        dates: e.first === e.last ? day(e.first) : t("tracker-graph:hop.dates", { first: day(e.first), last: day(e.last) }),
      };
      const text = e.src === a ? t("tracker-graph:hop.sent", what) : t("tracker-graph:hop.received", what);
      return e.coinjoin ? `${text} · ${t("tracker-graph:hop.coinjoin")}` : text;
    }),
    ...guesses.map((k) =>
      t("tracker-graph:hop.guess", { kind: k.kind, confidence: pct(k.confidence), transactions: t("tracker-graph:hop.transactions", { count: k.count }) }),
    ),
  ];
}

/** Where a label comes from, so a guess is never taken for a fact; "" without a label. */
export function namedBy(n: Pick<GraphNode, "label" | "label_source" | "label_confidence">, t: T): string {
  if (!n.label) return "";
  switch (n.label_source) {
    case "user":
      return t("tracker-graph:named.user");
    case "dataset":
      return t("tracker-graph:named.dataset");
    case "protocol":
      return t("tracker-graph:named.protocol");
    case "explorer":
      return t("tracker-graph:named.explorer");
    case "heuristic":
      return t("tracker-graph:named.heuristic", { confidence: pct(n.label_confidence ?? 0) });
    default:
      return "";
  }
}

/** The address an explorer link opens: its template with the address filled in. */
export const explorerUrl = (template: string, address: string) => template.replace("{}", encodeURIComponent(address.replace("bitcoincash:", "")));
