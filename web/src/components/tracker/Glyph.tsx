"use client";

import { useTranslation } from "react-i18next";
import { usePalette } from "@/hooks/usePalette";
import {
  chainMark,
  kindColor,
  kindLabelKey,
  kindShape,
  paint,
  partsOf,
  verdictColor,
  verdictShape,
  type GlyphPart,
  type VerdictLevel,
} from "@/lib/tracker/kinds";

/** A glyph on the 24 x 24 grid: `shape` is a glyph's name or a list of parts. Decorative unless `label` is given. */
export function Glyph({ shape, color, size = 16, label }: { shape: string | GlyphPart[]; color: string; size?: number; label?: string }) {
  const p = usePalette();
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className="inline-block shrink-0 align-middle"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {label && <title>{label}</title>}
      {partsOf(shape).map((part, i) => (
        <path
          key={i}
          d={part.d}
          fill={paint(part.fill, color, p)}
          stroke={paint(part.stroke, color, p)}
          strokeWidth={part.w ?? 0}
          strokeLinejoin="round"
          strokeDasharray={part.dash?.join(" ")}
        />
      ))}
    </svg>
  );
}

/** The glyph of a kind of address (mine, public, cex, mixer...). */
export function KindIcon({ kind, size = 16 }: { kind: string; size?: number }) {
  const p = usePalette();
  return <Glyph shape={kindShape(kind)} color={kindColor(kind, p)} size={size} />;
}

/** One of my wallets is a coin: sealed when private, open when public. */
export function Coin({ public: pub, size = 40 }: { public: boolean | number | undefined; size?: number }) {
  const { t } = useTranslation();
  const kind = pub ? "public" : "mine";
  const p = usePalette();
  return <Glyph shape={kindShape(kind)} color={kindColor(kind, p)} size={size} label={t(kindLabelKey(kind))} />;
}

/** A chain's mark, named for screen readers and on hover; nothing for an unknown chain. */
export function ChainMark({ chain, size = 16 }: { chain: string | undefined | null; size?: number }) {
  const mark = chainMark(chain);
  if (!mark) return null;
  return <Glyph shape={mark[1]} color="" size={size} label={mark[0]} />;
}

/** The glyph of a verdict level. */
export function VerdictIcon({ level, size = 20 }: { level: VerdictLevel; size?: number }) {
  const p = usePalette();
  return <Glyph shape={verdictShape(level)} color={verdictColor(level, p)} size={size} />;
}

/** A sample of a graph line, drawn as in the graphs, for the legend. */
export function LineSample({ color, dash, width = 1.5 }: { color: string; dash: number[]; width?: number }) {
  return (
    <svg viewBox="0 0 30 8" width={30} height={8} className="inline-block shrink-0" aria-hidden>
      <path d="M0 4h30" stroke={color} strokeWidth={width} strokeDasharray={dash.join(" ")} />
    </svg>
  );
}
