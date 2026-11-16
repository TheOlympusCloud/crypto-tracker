// Colour, 2D shape and wording per kind of address. Shape and the type written in every
// label repeat the colour for colour-blind readers.
export const KINDS = {
  mine: ["#3fb950", "ellipse", "My wallet, private"],
  public: ["#f0883e", "ellipse", "My wallet, public"],
  cex: ["#f85149", "round-rectangle", "Exchange"],
  dex: ["#a371f7", "diamond", "DEX / swap"],
  bridge: ["#58a6ff", "hexagon", "Bridge"],
  mixer: ["#e3b341", "triangle", "Mixer"],
  defi: ["#39c5cf", "round-diamond", "DeFi"],
  service: ["#db61a2", "round-rectangle", "Service"],
  other: ["#b1bac4", "round-tag", "Labelled"],
  external: ["#6e7681", "ellipse", "Unknown address"],
};
KINDS.unknown = KINDS.external;
// A funds source: one of my wallets counts as public or private, like everywhere else.
export const sourceKind = (s) => (s.kind === "own" ? (s.public ? "public" : "mine") : s.kind);
export const LINKS = { "common-input": "#e3b341", change: "#39c5cf", bridge: "#58a6ff" };

export const kindOf = (n) => (n.mine ? (n.public ? "public" : "mine") : n.category || "external");
export const short = (a) => (a.length > 14 ? `${a.slice(0, 7)}…${a.slice(-5)}` : a);
export const pct = (c) => `${Math.round(c * 100)}%`;
export const title = (n) => n.name || n.label || short(n.address);
