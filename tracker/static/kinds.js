// Colour, shape and wording per kind of address, guess and verdict: the one place for them.
// The colours repeat the custom properties in style.css; shape and the type written in every
// label repeat the colour for colour-blind readers.
export const STONE = { stone: "#0e0e0e", raised: "#171717", line: "#2e2e2e", lineStrong: "#6e6e6e", ink: "#ededed", quiet: "#b4b4b4" };
export const METAL = { verdigris: "#62c4a8", gold: "#e6b450", silver: "#c9c9c9", ember: "#f2914a", cinnabar: "#f4705e" };
export const MINERAL = { lapis: "#8fb2f0", amethyst: "#b79cf0", aqua: "#7fd0e0", rose: "#e58bc0", peridot: "#b8d072", sand: "#d8c9a8", slate: "#9a9a9a" };

// [colour, shape, wording]. The shape names a glyph below; both graphs draw it.
export const KINDS = {
  mine: [METAL.verdigris, "sealed-coin", "My wallet, private"],
  public: [METAL.gold, "open-coin", "My wallet, public"],
  cex: [MINERAL.lapis, "square", "Exchange"],
  dex: [MINERAL.amethyst, "diamond", "DEX / swap"],
  bridge: [MINERAL.aqua, "arch", "Bridge"],
  mixer: [MINERAL.rose, "star", "Mixer"],
  defi: [MINERAL.peridot, "hexagon", "DeFi"],
  service: [MINERAL.sand, "pentagon", "Service"],
  other: [STONE.quiet, "tag", "Labelled"],
  external: [MINERAL.slate, "ring", "Unknown address"],
};
KINDS.unknown = KINDS.external;
// A funds source: one of my wallets counts as public or private, like everywhere else.
export const sourceKind = (s) => (s.kind === "own" ? (s.public ? "public" : "mine") : s.kind);
// Bar segments that are hatched instead of solid, so the split reads without colour.
export const HATCHED = new Set(["public", "external", "unknown"]);

// Guesses that two addresses share an owner: colour, and a dash pattern of its own.
export const LINKS = {
  "common-input": METAL.ember,
  change: MINERAL.aqua,
  bridge: MINERAL.lapis,
  "exchange-flow": MINERAL.rose,
  swap: MINERAL.amethyst,
};
export const DASHES = { "common-input": [6, 4], change: [2, 3], bridge: [10, 4], "exchange-flow": [10, 3, 2, 3], swap: [5, 2, 1, 2, 1, 2] };
export const PATH = METAL.cinnabar; // the selected path, in both graphs
export const linkColor = (kind) => LINKS[kind] || STONE.ink;
export const linkDash = (kind) => DASHES[kind] || [4, 4];

// Verdict levels, as judged in app.js: [colour, glyph].
export const VERDICTS = {
  ok: [METAL.silver, "none"], // deliberately not green: "not found" is not "safe"
  warn: [METAL.ember, "tied"],
  bad: [METAL.cinnabar, "linked"],
  unknown: [MINERAL.slate, "unchecked"],
};

// Glyphs on a 24 × 24 grid. Each part is painted with the kind's colour ("c") or with stone
// ("s"), filled or stroked.
const circle = (r) => `M${12 - r} 12a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0z`;
export const GLYPHS = {
  "sealed-coin": [
    { d: circle(11), fill: "c" },
    { d: circle(8.7), stroke: "s", w: 0.6, dash: [0.6, 1.6] },
    { d: "M8.5 11h7v5.6h-7z", fill: "s" },
    { d: "M9.8 11V9.2a2.2 2.2 0 0 1 4.4 0V11", stroke: "s", w: 1.3 },
  ],
  "open-coin": [
    { d: circle(10.5), fill: "s", stroke: "c", w: 1.2 },
    { d: circle(8.4), stroke: "c", w: 0.6, dash: [2, 1.5] },
    { d: "M6.5 12c1.5-2.5 3.5-3.75 5.5-3.75S16 9.5 17.5 12c-1.5 2.5-3.5 3.75-5.5 3.75S8 14.5 6.5 12z", stroke: "c", w: 1.1 },
    { d: circle(1.6), fill: "c" },
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
};

const SVG = "http://www.w3.org/2000/svg";
const paint = (p, color) => ({ c: color, s: STONE.stone })[p] || "none";

// The glyph as an SVG element, for the page.
export function glyphEl(shape, color, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, class: "glyph", "aria-hidden": "true" })) svg.setAttribute(k, v);
  for (const part of GLYPHS[shape] || GLYPHS.ring) {
    const path = svg.appendChild(document.createElementNS(SVG, "path"));
    const attrs = { d: part.d, fill: paint(part.fill, color), stroke: paint(part.stroke, color), "stroke-width": part.w || 0, "stroke-linejoin": "round" };
    if (part.dash) attrs["stroke-dasharray"] = part.dash.join(" ");
    for (const [k, v] of Object.entries(attrs)) path.setAttribute(k, v);
  }
  return svg;
}

// The glyph on a 2D canvas context, in a size × size box at the origin.
export function drawGlyph(ctx, shape, color, size) {
  ctx.save();
  ctx.scale(size / 24, size / 24);
  ctx.lineJoin = "round";
  for (const part of GLYPHS[shape] || GLYPHS.ring) {
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

// The glyph as an image URL, for the flat graph. `dim` draws an address not expanded yet.
const urls = new Map();
export function glyphUrl(shape, color, dim = false) {
  const key = `${shape}|${color}|${dim}`;
  if (!urls.has(key)) {
    const canvas = Object.assign(document.createElement("canvas"), { width: 96, height: 96 });
    const ctx = canvas.getContext("2d");
    ctx.globalAlpha = dim ? 0.5 : 1;
    drawGlyph(ctx, shape, color, 96);
    urls.set(key, canvas.toDataURL());
  }
  return urls.get(key);
}

export const kindOf = (n) => (n.mine ? (n.public ? "public" : "mine") : n.category || "external");
export const short = (a) => (a.length > 14 ? `${a.slice(0, 7)}…${a.slice(-5)}` : a);
export const pct = (c) => `${Math.round(c * 100)}%`;
export const title = (n) => n.name || n.label || short(n.address);
