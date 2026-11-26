/**
 * Colour, shape and wording per kind of address, guess and verdict: the one place for them.
 * Shape and the type written in every label repeat the colour for colour-blind readers.
 * Colours that depend on the theme (my private wallets, verdicts, glyph paper and ink) are
 * resolved from the app's palette; the rest are fixed chart hues that read on both themes.
 */
import { COLORS, DARK_PALETTE, HUES } from "@/lib/palette";

export type Palette = typeof DARK_PALETTE;

export type Kind =
  | "mine"
  | "public"
  | "cex"
  | "dex"
  | "bridge"
  | "mixer"
  | "flagged"
  | "defi"
  | "service"
  | "other"
  | "external"
  | "unknown";

type Color = string | ((p: Palette) => string);
const fg = (p: Palette) => p.foreground;

/** [colour, shape, i18n key of the wording]. The shape names a glyph below; both graphs draw it. */
const KIND_TABLE: Record<Kind, [Color, string, string]> = {
  mine: [fg, "sealed-coin", "tracker:kind.mine"],
  public: [COLORS.bitcoin, "open-coin", "tracker:kind.public"],
  cex: [HUES.blue500, "square", "tracker:kind.cex"],
  dex: [HUES.violet500, "diamond", "tracker:kind.dex"],
  bridge: [HUES.cyan500, "arch", "tracker:kind.bridge"],
  mixer: [HUES.fuchsia600, "star", "tracker:kind.mixer"],
  flagged: [HUES.red600, "linked", "tracker:kind.flagged"],
  defi: [HUES.green600, "hexagon", "tracker:kind.defi"],
  service: [HUES.stone500, "pentagon", "tracker:kind.service"],
  other: [HUES.gray500, "tag", "tracker:kind.other"],
  external: [HUES.gray400, "ring", "tracker:kind.external"],
  unknown: [HUES.gray400, "ring", "tracker:kind.external"],
};

/** The kinds a counterparty can be, in the order the legend lists them. */
export const COUNTERPARTY_KINDS: Kind[] = ["cex", "dex", "bridge", "mixer", "flagged", "defi", "service", "other", "external"];

const resolve = (c: Color, p: Palette) => (typeof c === "function" ? c(p) : c);
const asKind = (k: string): Kind => (k in KIND_TABLE ? (k as Kind) : "other");

export const kindColor = (kind: string, p: Palette) => resolve(KIND_TABLE[asKind(kind)][0], p);
export const kindShape = (kind: string) => KIND_TABLE[asKind(kind)][1];
export const kindLabelKey = (kind: string) => KIND_TABLE[asKind(kind)][2];

/** Bar segments that are hatched instead of solid, so the split reads without colour. */
export const HATCHED = new Set<string>(["public", "external", "unknown"]);

/** Guesses that two addresses share an owner: colour, and a dash pattern of its own. */
export const LINKS: Record<string, string> = {
  "common-input": HUES.amber500,
  change: HUES.cyan500,
  bridge: HUES.blue500,
  "co-signer": HUES.green400,
  "deposit-address": HUES.fuchsia600,
  "exchange-flow": HUES.pink500,
  "fee-payer": HUES.blue300,
  mixer: HUES.fuchsia400,
  name: HUES.emerald600,
  swap: HUES.violet500,
  "bridge-record": HUES.yellow600,
};
const DASHES: Record<string, number[]> = {
  "common-input": [6, 4],
  change: [2, 3],
  bridge: [10, 4],
  "co-signer": [6, 2, 1, 2],
  "deposit-address": [8, 2, 2, 2],
  "exchange-flow": [10, 3, 2, 3],
  "fee-payer": [1, 3],
  mixer: [1, 2, 1, 6],
  name: [6, 2, 6, 6],
  swap: [5, 2, 1, 2, 1, 2],
  "bridge-record": [12, 2, 1, 2],
};
/** The selected path, in both graphs: one fat orange line. */
export const PATH = COLORS.bitcoin;
export const linkColor = (kind: string, p: Palette) => LINKS[kind] ?? p.foreground;
export const linkDash = (kind: string) => DASHES[kind] ?? [4, 4];

export type VerdictLevel = "ok" | "warn" | "bad" | "unknown";
/** Verdict levels: [colour, glyph]. "ok" is deliberately not green: "not found" is not "safe". */
const VERDICT_TABLE: Record<VerdictLevel, [Color, string]> = {
  ok: [(p) => p.muted, "none"],
  warn: [(p) => p.warning, "tied"],
  bad: [(p) => p.danger, "linked"],
  unknown: [HUES.gray500, "unchecked"],
};
export const verdictColor = (level: VerdictLevel, p: Palette) => resolve(VERDICT_TABLE[level][0], p);
export const verdictShape = (level: VerdictLevel) => VERDICT_TABLE[level][1];

/** How far a token can be trusted (tracker/tokens.py): [colour, glyph, i18n key]. "verified" only means a token list names it. */
const TRUST_TABLE: Record<string, [Color, string, string]> = {
  verified: [HUES.cyan500, "seal", "tracker:trust.verified"],
  unknown: [HUES.gray500, "unchecked", "tracker:trust.unknown"],
  scam: [(p) => p.danger, "linked", "tracker:trust.scam"],
};
const trustRow = (level: string) => TRUST_TABLE[level] ?? TRUST_TABLE.unknown!;
export const trustColor = (level: string, p: Palette) => resolve(trustRow(level)[0], p);
export const trustShape = (level: string) => trustRow(level)[1];
export const trustLabelKey = (level: string) => trustRow(level)[2];

/** One part of a glyph on a 24 x 24 grid, painted with the kind's colour ("c"), paper ("s"), ink ("k") or a colour of its own. */
export interface GlyphPart {
  d: string;
  fill?: string;
  stroke?: string;
  w?: number;
  dash?: number[];
}

const circle = (r: number) => `M${12 - r} 12a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0z`;
export const GLYPHS: Record<string, GlyphPart[]> = {
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

/** A glyph's parts: `shape` is a glyph's name or a list of parts. */
export const partsOf = (shape: string | GlyphPart[]) => (Array.isArray(shape) ? shape : (GLYPHS[shape] ?? GLYPHS.ring!));
/** The colour a part is painted with. */
export const paint = (part: string | undefined, color: string, p: Palette) =>
  part === "c" ? color : part === "s" ? p.cardBg : part === "k" ? p.foreground : (part ?? "none");

// Chain marks on the same grid: a disc in the chain's colour with a white sign. Drawn here,
// not the official logos: just enough of them to tell the chains apart at 14 px.
const disc = (fill: string): GlyphPart => ({ d: circle(11.5), fill });
const B = "M8.6 6.8v10.7M7.2 6.8h5.2a2.6 2.6 0 0 1 0 5.2H8.6M8.6 12h4.6a2.75 2.75 0 0 1 0 5.5H7.2M10 5v1.8M12.4 5v1.8M10 17.5v1.8M12.4 17.5v1.8";
const ETH = "M12 3.8 6.8 12.4 12 15.4l5.2-3zM12 16.4 6.8 13.4 12 20.3l5.2-6.9z";
const W = "#ffffff";
/** [name, parts]. Keys are the chains of tracker/chains.py; a Python test checks that none is missing. */
export const CHAINS: Record<string, [string, GlyphPart[]]> = {
  bitcoin: ["Bitcoin", [disc("#f7931a"), { d: B, stroke: W, w: 1.8 }]],
  litecoin: ["Litecoin", [disc("#345d9d"), { d: "M10.4 6.5v10.6h6.1M7.6 14l6-2.6", stroke: W, w: 2 }]],
  bitcoincash: ["Bitcoin Cash", [disc("#0ac18e"), { d: B, stroke: W, w: 1.8 }]],
  dogecoin: ["Dogecoin", [disc("#c2a633"), { d: "M9.2 6.8h2.6a5.2 5.2 0 0 1 0 10.4H9.2zM7 12h5.2", stroke: W, w: 1.9 }]],
  ethereum: ["Ethereum", [disc("#627eea"), { d: ETH, fill: W }]],
  evm: ["EVM", [disc(HUES.gray500), { d: ETH, stroke: W, w: 1.3 }]],
  base: ["Base", [disc("#0052ff"), { d: circle(6.2), fill: W }, { d: "M5.6 11h7.6v2H5.6z", fill: "#0052ff" }]],
  arbitrum: ["Arbitrum", [disc("#213147"), { d: "M7.2 17.6 12 6.2l4.8 11.4", stroke: "#12aaff", w: 2.1 }, { d: "M9.7 17.6 12 12.3l2.3 5.3", stroke: W, w: 1.6 }]],
  optimism: ["Optimism", [disc("#ff0420"), { d: "M5.6 12a2.9 2.9 0 1 0 5.8 0a2.9 2.9 0 1 0-5.8 0zM13.6 15.2V8.8h2.5a1.9 1.9 0 0 1 0 3.8h-2.5", stroke: W, w: 1.8 }]],
  polygon: ["Polygon", [disc("#8247e5"), { d: "M12 6.2l5 2.9v5.8l-5 2.9-5-2.9V9.1z", stroke: W, w: 2 }]],
  bsc: ["BNB Chain", [disc("#f0b90b"), { d: "M12 6.6 17.4 12 12 17.4 6.6 12z", stroke: W, w: 1.8 }, { d: "M12 10.2 13.8 12 12 13.8 10.2 12z", fill: W }]],
  avalanche: ["Avalanche", [disc("#e84142"), { d: "M11.1 6.9a1 1 0 0 1 1.8 0l5 8.9a.8.8 0 0 1-.7 1.2H6.8a.8.8 0 0 1-.7-1.2z", fill: W }]],
  tron: ["Tron", [disc("#eb0029"), { d: "M6.4 6.9 17.8 9.6 11.8 18.2zM6.4 6.9l5.6 5.5 5.8-2.8M12 12.4l-.2 5.8", stroke: W, w: 1.3 }]],
  solana: ["Solana", [disc("#14100d"), { d: "M8.4 7.4h9.4l-2.2 2.1H6.2z", fill: "#14f195" }, { d: "M6.2 11h9.4l2.2 2.1H8.4z", fill: "#8a8cf0" }, { d: "M8.4 14.6h9.4l-2.2 2.1H6.2z", fill: "#9945ff" }]],
};

/** A chain's mark: [name, parts]. A chain added through CT_CHAINS is an EVM chain without a mark of its own. */
export const chainMark = (key: string | undefined | null): [string, GlyphPart[]] | null =>
  key ? (CHAINS[key] ?? [key, CHAINS.evm![1]]) : null;
export const chainLabel = (key: string | undefined | null) => chainMark(key)?.[0] ?? "";

interface Addressed {
  address: string;
  mine?: number | boolean;
  public?: number | boolean;
  category?: string | null;
  name?: string;
  label?: string | null;
  chain?: string;
  icon?: string;
}

/** The chain mark of an address: `icon` where the server narrowed an EVM address to one chain. */
export const chainOf = (n: { chain?: string; icon?: string }) => n.icon || n.chain || "";
/** What an address is: one of my wallets (private or public), a labelled kind, or a stranger. */
export const kindOf = (n: Pick<Addressed, "mine" | "public" | "category">): Kind =>
  n.mine ? (n.public ? "public" : "mine") : asKind(n.category || "external");
/** A funds source: one of my wallets counts as public or private, like everywhere else. */
export const sourceKind = (s: { kind: string; public?: boolean }): Kind => (s.kind === "own" ? (s.public ? "public" : "mine") : asKind(s.kind));
export const short = (a: string) => (a.length > 14 ? `${a.slice(0, 7)}…${a.slice(-5)}` : a);
export const pct = (c: number) => `${Math.round(c * 100)}%`;
export const title = (n: Pick<Addressed, "address" | "name" | "label">) => n.name || n.label || short(n.address);

/**
 * What an address is, in words: the label's own type when it has one ("DEX aggregator"), else
 * the general kind, translated by `t`. A label that is a guess says so.
 */
export function typeOf(
  n: { label_type?: string; type?: string; label_guess?: boolean; guess?: boolean; kind?: string; public?: boolean | number; mine?: number | boolean; category?: string | null },
  t: (key: string) => string,
): string {
  const kind = n.kind ? sourceKind({ kind: n.kind, public: Boolean(n.public) }) : kindOf(n);
  const what = n.label_type || n.type || t(kindLabelKey(kind));
  return n.label_guess || n.guess ? `${what} ${t("tracker:kind.guess")}` : what;
}
