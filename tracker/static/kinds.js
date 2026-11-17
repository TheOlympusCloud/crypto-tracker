// Colour, shape and wording per kind of address, guess and verdict: the one place for them.
// The colours repeat the custom properties in style.css; shape and the type written in every
// label repeat the colour for colour-blind readers.
export const PAPER = { page: "#f8eee8", card: "#fdf8f5", blush: "#f9e2e1", line: "#e2cfc3", lineStrong: "#857a73", ink: "#14100d", quiet: "#5c514a" };
// What a wallet is, and what was found about it. Orange is the loud one: public, and the path.
export const SIGNAL = { private: "#14100d", public: "#ff5400", none: "#5c514a", tied: "#8a5a00", linked: "#c4161c" };
// Counterparty types: each dark enough to read on paper.
export const HUES = { blue: "#1a54e4", purple: "#8134cd", teal: "#006f7a", magenta: "#c2187a", green: "#2b7230", brown: "#8a5a2b", grey: "#857a73" };

// [colour, shape, wording]. The shape names a glyph below; both graphs draw it.
export const KINDS = {
  mine: [SIGNAL.private, "sealed-coin", "My wallet, private"],
  public: [SIGNAL.public, "open-coin", "My wallet, public"],
  cex: [HUES.blue, "square", "Exchange"],
  dex: [HUES.purple, "diamond", "DEX / swap"],
  bridge: [HUES.teal, "arch", "Bridge"],
  mixer: [HUES.magenta, "star", "Mixer"],
  flagged: [SIGNAL.linked, "linked", "Flagged: scam, hack or sanctions"],
  defi: [HUES.green, "hexagon", "DeFi"],
  service: [HUES.brown, "pentagon", "Service"],
  other: [PAPER.quiet, "tag", "Labelled"],
  external: [HUES.grey, "ring", "Unknown address"],
};
KINDS.unknown = KINDS.external;
// A funds source: one of my wallets counts as public or private, like everywhere else.
export const sourceKind = (s) => (s.kind === "own" ? (s.public ? "public" : "mine") : s.kind);
// Bar segments that are hatched instead of solid, so the split reads without colour.
export const HATCHED = new Set(["public", "external", "unknown"]);

// Guesses that two addresses share an owner: colour, and a dash pattern of its own.
export const LINKS = {
  "common-input": SIGNAL.tied,
  change: HUES.teal,
  bridge: HUES.blue,
  "exchange-flow": HUES.magenta,
  swap: HUES.purple,
};
export const DASHES = { "common-input": [6, 4], change: [2, 3], bridge: [10, 4], "exchange-flow": [10, 3, 2, 3], swap: [5, 2, 1, 2, 1, 2] };
export const PATH = SIGNAL.public; // the selected path, in both graphs: one fat orange line
export const linkColor = (kind) => LINKS[kind] || PAPER.ink;
export const linkDash = (kind) => DASHES[kind] || [4, 4];

// Verdict levels, as judged in app.js: [colour, glyph].
export const VERDICTS = {
  ok: [SIGNAL.none, "none"], // deliberately not green: "not found" is not "safe"
  warn: [SIGNAL.tied, "tied"],
  bad: [SIGNAL.linked, "linked"],
  unknown: [HUES.grey, "unchecked"],
};
// How far a token can be trusted, as judged in tokens.py: [colour, glyph, wording].
// "verified" only means that a token list names this contract.
export const TRUST = {
  verified: [HUES.teal, "seal", "verified"],
  unknown: [HUES.grey, "unchecked", "unknown"],
  scam: [SIGNAL.linked, "linked", "fake · worth 0"],
};

// Glyphs on a 24 × 24 grid. Each part is painted with the kind's colour ("c"), with paper
// ("s") or with ink ("k"), filled or stroked.
const circle = (r) => `M${12 - r} 12a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0z`;
export const GLYPHS = {
  "sealed-coin": [
    { d: circle(11), fill: "c" },
    { d: circle(8.7), stroke: "s", w: 0.6, dash: [0.6, 1.6] },
    { d: "M8.5 11h7v5.6h-7z", fill: "s" },
    { d: "M9.8 11V9.2a2.2 2.2 0 0 1 4.4 0V11", stroke: "s", w: 1.3 },
  ],
  "open-coin": [
    { d: circle(10.5), fill: "c", stroke: "k", w: 1 },
    { d: circle(8.2), stroke: "k", w: 0.6, dash: [2, 1.5] },
    { d: "M6.5 12c1.5-2.5 3.5-3.75 5.5-3.75S16 9.5 17.5 12c-1.5 2.5-3.5 3.75-5.5 3.75S8 14.5 6.5 12z", fill: "s", stroke: "k", w: 1.1 },
    { d: circle(1.7), fill: "k" },
  ],
  square: [{ d: "M4 4h16v16H4z", fill: "c" }],
  diamond: [{ d: "M12 1.5 22.5 12 12 22.5 1.5 12z", fill: "c" }],
  arch: [{ d: "M3 21v-8a9 9 0 0 1 18 0v8h-5.5v-8a3.5 3.5 0 0 0-7 0v8z", fill: "c" }],
  star: [{ d: "M12 1l3.2 7.8L23 12l-7.8 3.2L12 23l-3.2-7.8L1 12l7.8-3.2z", fill: "c" }],
  hexagon: [{ d: "M12 2l8.66 5v10L12 22l-8.66-5V7z", fill: "c" }],
  pentagon: [{ d: "M12 2l9.5 6.9-3.63 11.1H6.13L2.5 8.9z", fill: "c" }],
  tag: [{ d: "M3 5.5h11.5L21 12l-6.5 6.5H3z", fill: "c" }],
  ring: [{ d: circle(6), stroke: "c", w: 2.4 }],
  none: [
    { d: circle(8.5), stroke: "c", w: 1.8 },
    { d: "M8 12h8", stroke: "c", w: 1.8 },
  ],
  tied: [
    { d: "M12 3.5l9.5 17h-19z", stroke: "c", w: 1.8 },
    { d: "M12 10v5.5M12 17.6v1.4", stroke: "c", w: 1.8 },
  ],
  linked: [
    { d: "M8.2 2.5h7.6l5.7 5.7v7.6l-5.7 5.7H8.2l-5.7-5.7V8.2z", fill: "c" },
    { d: "M8.4 8.4l7.2 7.2M15.6 8.4l-7.2 7.2", stroke: "s", w: 2.2 },
  ],
  unchecked: [
    { d: circle(8.5), stroke: "c", w: 1.8, dash: [2.2, 2.4] },
    { d: circle(1.4), fill: "c" },
  ],
  seal: [
    { d: circle(10), fill: "c" },
    { d: "M7.6 12.3l3 3 5.8-6.2", stroke: "s", w: 2.2 },
  ],
};

// Chain marks on the same grid: a disc in the chain's colour with a white sign. Drawn here,
// not the official logos: just enough of them to tell the chains apart at 14 px.
const disc = (fill) => ({ d: circle(11.5), fill });
const B = "M8.6 6.8v10.7M7.2 6.8h5.2a2.6 2.6 0 0 1 0 5.2H8.6M8.6 12h4.6a2.75 2.75 0 0 1 0 5.5H7.2M10 5v1.8M12.4 5v1.8M10 17.5v1.8M12.4 17.5v1.8";
const ETH = "M12 3.8 6.8 12.4 12 15.4l5.2-3zM12 16.4 6.8 13.4 12 20.3l5.2-6.9z";
const W = "#ffffff";
// [name, parts]. Keys are the chains of tracker/chains.py; tests check that none is missing.
export const CHAINS = {
  bitcoin: ["Bitcoin", [disc("#f7931a"), { d: B, stroke: W, w: 1.8 }]],
  litecoin: ["Litecoin", [disc("#345d9d"), { d: "M10.4 6.5v10.6h6.1M7.6 14l6-2.6", stroke: W, w: 2 }]],
  bitcoincash: ["Bitcoin Cash", [disc("#0ac18e"), { d: B, stroke: W, w: 1.8 }]],
  dogecoin: ["Dogecoin", [disc("#c2a633"), { d: "M9.2 6.8h2.6a5.2 5.2 0 0 1 0 10.4H9.2zM7 12h5.2", stroke: W, w: 1.9 }]],
  ethereum: ["Ethereum", [disc("#627eea"), { d: ETH, fill: W }]],
  evm: ["EVM, any chain", [disc(PAPER.quiet), { d: ETH, stroke: W, w: 1.3 }]],
  base: ["Base", [disc("#0052ff"), { d: circle(6.2), fill: W }, { d: "M5.6 11h7.6v2H5.6z", fill: "#0052ff" }]],
  arbitrum: ["Arbitrum", [disc("#213147"), { d: "M7.2 17.6 12 6.2l4.8 11.4", stroke: "#12aaff", w: 2.1 }, { d: "M9.7 17.6 12 12.3l2.3 5.3", stroke: W, w: 1.6 }]],
  optimism: ["Optimism", [disc("#ff0420"), { d: "M5.6 12a2.9 2.9 0 1 0 5.8 0a2.9 2.9 0 1 0-5.8 0zM13.6 15.2V8.8h2.5a1.9 1.9 0 0 1 0 3.8h-2.5", stroke: W, w: 1.8 }]],
  polygon: ["Polygon", [disc("#8247e5"), { d: "M12 6.2l5 2.9v5.8l-5 2.9-5-2.9V9.1z", stroke: W, w: 2 }]],
  bsc: ["BNB Chain", [disc("#f0b90b"), { d: "M12 6.6 17.4 12 12 17.4 6.6 12z", stroke: W, w: 1.8 }, { d: "M12 10.2 13.8 12 12 13.8 10.2 12z", fill: W }]],
  avalanche: ["Avalanche", [disc("#e84142"), { d: "M11.1 6.9a1 1 0 0 1 1.8 0l5 8.9a.8.8 0 0 1-.7 1.2H6.8a.8.8 0 0 1-.7-1.2z", fill: W }]],
  tron: ["Tron", [disc("#eb0029"), { d: "M6.4 6.9 17.8 9.6 11.8 18.2zM6.4 6.9l5.6 5.5 5.8-2.8M12 12.4l-.2 5.8", stroke: W, w: 1.3 }]],
  solana: ["Solana", [disc(PAPER.ink), { d: "M8.4 7.4h9.4l-2.2 2.1H6.2z", fill: "#14f195" }, { d: "M6.2 11h9.4l2.2 2.1H8.4z", fill: "#8a8cf0" }, { d: "M8.4 14.6h9.4l-2.2 2.1H6.2z", fill: "#9945ff" }]],
};
// The chain mark of an address: `icon` where the server narrowed an EVM address to one chain.
export const chainOf = (n) => n.icon || n.chain;
// A chain added through CT_CHAINS is an EVM chain without a mark of its own.
const chainMark = (key) => (key ? CHAINS[key] || [key, CHAINS.evm[1]] : null);
export const chainLabel = (key) => chainMark(key)?.[0] || "";

const SVG = "http://www.w3.org/2000/svg";
// A part is painted with the kind's colour ("c"), paper ("s"), ink ("k") or a colour of its own.
const paint = (p, color) => ({ c: color, s: PAPER.card, k: PAPER.ink })[p] || p || "none";
const partsOf = (shape) => (Array.isArray(shape) ? shape : GLYPHS[shape] || GLYPHS.ring);

// The glyph as an SVG element, for the page. `shape` is a glyph's name or a list of parts.
export function glyphEl(shape, color, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, class: "glyph", "aria-hidden": "true" })) svg.setAttribute(k, v);
  for (const part of partsOf(shape)) {
    const path = svg.appendChild(document.createElementNS(SVG, "path"));
    const attrs = { d: part.d, fill: paint(part.fill, color), stroke: paint(part.stroke, color), "stroke-width": part.w || 0, "stroke-linejoin": "round" };
    if (part.dash) attrs["stroke-dasharray"] = part.dash.join(" ");
    for (const [k, v] of Object.entries(attrs)) path.setAttribute(k, v);
  }
  return svg;
}

// A chain's mark, named for screen readers and on hover; nothing for an unknown chain.
export function chainEl(key, size = 16) {
  const mark = chainMark(key);
  if (!mark) return null;
  const svg = glyphEl(mark[1], null, size);
  svg.setAttribute("class", "glyph chainmark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", mark[0]);
  svg.removeAttribute("aria-hidden");
  svg.prepend(Object.assign(document.createElementNS(SVG, "title"), { textContent: mark[0] }));
  return svg;
}

// The glyph on a 2D canvas context, in a size × size box at the origin.
export function drawGlyph(ctx, shape, color, size) {
  ctx.save();
  ctx.scale(size / 24, size / 24);
  ctx.lineJoin = "round";
  for (const part of partsOf(shape)) {
    const path = new Path2D(part.d);
    if (part.fill) {
      ctx.fillStyle = paint(part.fill, color);
      ctx.fill(path);
    }
    if (part.stroke) {
      ctx.strokeStyle = paint(part.stroke, color);
      ctx.lineWidth = part.w;
      ctx.setLineDash(part.dash || []);
      ctx.stroke(path);
    }
  }
  ctx.restore();
}

// A chain's mark on a paper rim, in a size × size box at the origin: a badge on another glyph.
export function drawChain(ctx, key, size) {
  const mark = chainMark(key);
  if (!mark) return;
  drawGlyph(ctx, [{ d: circle(12), fill: "s" }], null, size);
  ctx.save();
  ctx.translate(size / 10, size / 10);
  drawGlyph(ctx, mark[1], null, size * 0.8);
  ctx.restore();
}

// The glyph as an image URL, for the flat graph. `dim` draws an address not expanded yet;
// `chain` adds that chain's mark at its lower right.
const urls = new Map();
export function glyphUrl(shape, color, dim = false, chain = "") {
  const key = `${shape}|${color}|${dim}|${chain}`;
  if (!urls.has(key)) {
    const canvas = Object.assign(document.createElement("canvas"), { width: 96, height: 96 });
    const ctx = canvas.getContext("2d");
    ctx.globalAlpha = dim ? 0.5 : 1;
    drawGlyph(ctx, shape, color, 96);
    ctx.translate(54, 54);
    drawChain(ctx, chain, 42);
    urls.set(key, canvas.toDataURL());
  }
  return urls.get(key);
}

export const kindOf = (n) => (n.mine ? (n.public ? "public" : "mine") : n.category || "external");
export const short = (a) => (a.length > 14 ? `${a.slice(0, 7)}…${a.slice(-5)}` : a);
export const pct = (c) => `${Math.round(c * 100)}%`;
export const title = (n) => n.name || n.label || short(n.address);
// What an address is, in words: the label's own type when it has one ("DEX aggregator",
// "exchange deposit address"), else the general kind. A label that is a guess says so.
export const typeOf = (n) => `${n.label_type || n.type || KINDS[n.kind ? sourceKind(n) : kindOf(n)][2]}${n.label_guess || n.guess ? " (guess)" : ""}`;
