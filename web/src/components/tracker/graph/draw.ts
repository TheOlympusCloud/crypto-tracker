import { chainMark, paint, partsOf, type GlyphPart, type Palette } from "@/lib/tracker/kinds";
import type { Graph, GraphNode } from "@/lib/tracker/types";

/** What both graphs are drawn with: the theme's colours and the UI language. */
export interface Look {
  palette: Palette;
  t: (key: string) => string;
}

/** The 3D and the flat graph share this interface. `setLook` takes effect at the next `setData`. */
export interface Renderer {
  setLook(look: Look): void;
  setData(graph: Graph, opts: { minConf: number }): void;
  highlight(path: string[]): void;
  focus(address: string): void;
  setActive(on: boolean): void;
  destroy(): void;
}

export interface RendererOptions {
  onSelect: (node: GraphNode | null) => void;
  /** Where the 3D graph writes what is under the pointer: placed in the graph's box. */
  tip: HTMLElement;
}

/** The glyph on a 2D canvas context, in a size x size box at the origin. */
export function drawGlyph(ctx: CanvasRenderingContext2D, shape: string | GlyphPart[], color: string, size: number, p: Palette) {
  ctx.save();
  ctx.scale(size / 24, size / 24);
  ctx.lineJoin = "round";
  for (const part of partsOf(shape)) {
    const path = new Path2D(part.d);
    if (part.fill) {
      ctx.fillStyle = paint(part.fill, color, p);
      ctx.fill(path);
    }
    if (part.stroke) {
      ctx.strokeStyle = paint(part.stroke, color, p);
      ctx.lineWidth = part.w ?? 1;
      ctx.setLineDash(part.dash ?? []);
      ctx.stroke(path);
    }
  }
  ctx.restore();
}

const RIM: GlyphPart[] = [{ d: "M0 12a12 12 0 1 0 24 0a12 12 0 1 0 -24 0z", fill: "s" }];

/** A chain's mark on a paper rim, in a size x size box at the origin: a badge on another glyph. */
export function drawChain(ctx: CanvasRenderingContext2D, key: string, size: number, p: Palette) {
  const mark = chainMark(key);
  if (!mark) return;
  drawGlyph(ctx, RIM, "", size, p);
  ctx.save();
  ctx.translate(size / 10, size / 10);
  drawGlyph(ctx, mark[1], "", size * 0.8, p);
  ctx.restore();
}

const urls = new Map<string, string>();
/**
 * The glyph as an image URL, for the flat graph. `dim` draws an address not expanded yet;
 * `chain` adds that chain's mark at its lower right.
 */
export function glyphUrl(shape: string, color: string, p: Palette, dim = false, chain = "") {
  const key = `${shape}|${color}|${p.cardBg}|${p.foreground}|${dim}|${chain}`;
  let url = urls.get(key);
  if (url === undefined) {
    const canvas = Object.assign(document.createElement("canvas"), { width: 96, height: 96 });
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.globalAlpha = dim ? 0.5 : 1;
      drawGlyph(ctx, shape, color, 96, p);
      ctx.translate(54, 54);
      drawChain(ctx, chain, 42, p);
    }
    url = canvas.toDataURL();
    urls.set(key, url);
  }
  return url;
}

/** The app's font families (next/font sets them on the body), for labels drawn on canvases. */
export function canvasFonts() {
  const css = getComputedStyle(document.body);
  const family = (name: string, fallback: string) => [css.getPropertyValue(name).trim(), fallback].filter(Boolean).join(", ");
  return { sans: family("--font-geist-sans", "system-ui, sans-serif"), mono: family("--font-geist-mono", "ui-monospace, monospace") };
}

/** Canvases do not wait for web fonts by themselves. */
export function loadFonts(): Promise<unknown> {
  return Promise.resolve()
    .then(() => {
      const { sans, mono } = canvasFonts();
      return Promise.all([`700 16px ${sans}`, `500 16px ${sans}`, `400 16px ${mono}`].map((f) => document.fonts.load(f)));
    })
    .catch(() => undefined); // no font loading API here: draw with what there is
}

export const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The element that becomes visible fades in, unless motion is unwanted. */
export function fadeIn(el: HTMLElement) {
  if (!reducedMotion()) el.animate?.([{ opacity: 0 }, { opacity: 1 }], { duration: 400, easing: "ease-out" });
}
