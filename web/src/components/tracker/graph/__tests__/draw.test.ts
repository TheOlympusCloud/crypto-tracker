// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DARK_PALETTE, LIGHT_PALETTE } from "@/lib/palette";
import { drawChain, drawGlyph, glyphUrl } from "../draw";

// jsdom draws nothing: a context that records what it is asked to paint.
function recorder() {
  const painted: string[] = [];
  const ctx = {
    save: vi.fn(),
    restore: vi.fn(),
    scale: vi.fn(),
    translate: vi.fn(),
    setLineDash: vi.fn(),
    fill: vi.fn(function (this: { fillStyle: string }) {
      painted.push(`fill ${this.fillStyle}`);
    }),
    stroke: vi.fn(function (this: { strokeStyle: string; lineWidth: number }) {
      painted.push(`stroke ${this.strokeStyle} ${this.lineWidth}`);
    }),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    lineJoin: "",
    globalAlpha: 1,
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D & typeof ctx, painted };
}

beforeEach(() => {
  vi.stubGlobal("Path2D", class {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("drawGlyph", () => {
  it("paints each part with the kind's colour, the card or the ink of the theme", () => {
    const { ctx, painted } = recorder();
    drawGlyph(ctx, "open-coin", "#f7931a", 48, LIGHT_PALETTE);
    expect(ctx.scale).toHaveBeenCalledWith(2, 2);
    expect(painted).toEqual([
      "fill #f7931a",
      `stroke ${LIGHT_PALETTE.foreground} 1`,
      `stroke ${LIGHT_PALETTE.foreground} 0.6`,
      `fill ${LIGHT_PALETTE.cardBg}`,
      `stroke ${LIGHT_PALETTE.foreground} 1.1`,
      `fill ${LIGHT_PALETTE.foreground}`,
    ]);
    expect(ctx.setLineDash).toHaveBeenCalledWith([2, 1.5]);
  });

  it("draws an unknown shape as a ring", () => {
    const { ctx, painted } = recorder();
    drawGlyph(ctx, "no-such-glyph", "#123456", 24, DARK_PALETTE);
    expect(painted).toEqual(["stroke #123456 2.4"]);
  });
});

describe("drawChain", () => {
  it("draws a chain's mark on a card-coloured rim, and nothing without a chain", () => {
    const { ctx, painted } = recorder();
    drawChain(ctx, "", 20, DARK_PALETTE);
    expect(painted).toEqual([]);
    drawChain(ctx, "bitcoin", 20, DARK_PALETTE);
    expect(painted).toEqual([`fill ${DARK_PALETTE.cardBg}`, "fill #f7931a", "stroke #ffffff 1.8"]);
  });
});

describe("glyphUrl", () => {
  it("draws each glyph once per look, dimmed when asked", () => {
    const { ctx } = recorder();
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(() => `data:${getContext.mock.calls.length}`);
    const first = glyphUrl("star", "#c026d3", DARK_PALETTE, true, "evm");
    expect(ctx.globalAlpha).toBe(0.5);
    expect(glyphUrl("star", "#c026d3", DARK_PALETTE, true, "evm")).toBe(first);
    expect(getContext).toHaveBeenCalledTimes(1);
    expect(glyphUrl("star", "#c026d3", LIGHT_PALETTE, true, "evm")).not.toBe(first);
  });
});
