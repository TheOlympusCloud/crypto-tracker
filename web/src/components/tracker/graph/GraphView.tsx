"use client";

import { useEffect, useEffectEvent, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { usePalette } from "@/hooks/usePalette";
import { fold } from "@/lib/tracker/fold";
import type { Palette } from "@/lib/tracker/kinds";
import type { Graph, GraphNode } from "@/lib/tracker/types";
import { useTracker, type PanelState } from "../TrackerProvider";
import { loadFonts, type Look, type Renderer } from "./draw";
import { Legend } from "./Legend";

type Mode = "2d" | "3d";

const NARROW = "(max-width: 760px)";
const narrowQuery = () => (typeof window.matchMedia === "function" ? window.matchMedia(NARROW) : null);
const onNarrow = (changed: () => void) => {
  const q = narrowQuery();
  q?.addEventListener("change", changed);
  return () => q?.removeEventListener("change", changed);
};
const isNarrow = () => narrowQuery()?.matches ?? false;

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";

/** The graph view: 3D where WebGL works, flat on narrow screens and as the fallback. */
export function GraphView({ active }: { active: boolean }) {
  const { t } = useTranslation();
  const palette = usePalette();
  const { graph, scope, collapsed, shownPaths, minConf, setMinConf, spam, setSpam, focus, panel, selectNode, openPanel, closePanel, fail } = useTracker();
  const narrow = useSyncExternalStore(onNarrow, isNarrow, () => false);
  const [chosen, setChosen] = useState<Mode | null>(null);
  const [noWebGL, setNoWebGL] = useState(false);
  const mode: Mode = noWebGL ? "2d" : (chosen ?? (narrow ? "2d" : "3d"));

  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  // Each renderer is made on first use and kept, with its scene, until the view goes away.
  const renderers = useRef<Partial<Record<Mode, Renderer>>>({});
  const [made, setMade] = useState(0);
  const seen = useRef(new WeakMap<Renderer, { graph: Graph; minConf: number; palette: Palette; t: Look["t"] }>());
  const focused = useRef(0);

  // What the graph shows: the data, less what is folded away, but never a hop of a shown path.
  // The looked-up address's graph object stays the same when only its state changes (Watch, Track).
  const shown = scope?.graph ?? graph;
  const drawn = useMemo(() => fold(shown, collapsed, shownPaths.flat(), minConf), [shown, collapsed, shownPaths, minConf]);

  // A tap beside the graph does not end a lookup.
  const onSelect = useEffectEvent((n: GraphNode | null) => {
    if (n) selectNode(n.address);
    else if (scope) openPanel({ kind: "lookup" });
    else closePanel();
  });
  const onFail = useEffectEvent((e: unknown) => fail(e));

  useEffect(() => {
    const el = canvas.current;
    const tipEl = tip.current;
    if (!active || renderers.current[mode] || !el || !tipEl) return;
    let gone = false;
    const options = { onSelect: (n: GraphNode | null) => onSelect(n), tip: tipEl };
    loadFonts()
      .then(() => (mode === "3d" ? import("./graph3d").then((m) => !gone && m.create3d(el, options)) : import("./graph2d").then((m) => !gone && m.create2d(el, options))))
      .then((r) => {
        if (!r) return;
        if (gone) return r.destroy();
        renderers.current[mode] = r;
        setMade((n) => n + 1);
      })
      .catch((e: unknown) => {
        if (gone) return;
        if (mode === "3d") setNoWebGL(true); // no WebGL here: the flat graph instead
        else onFail(e);
      });
    return () => {
      gone = true;
    };
  }, [active, mode]);

  useEffect(() => {
    const all = renderers.current;
    return () => Object.values(all).forEach((r) => r.destroy());
  }, []);

  useEffect(() => {
    for (const [name, r] of Object.entries(renderers.current)) r.setActive(active && name === mode);
  }, [active, mode, made]);

  useEffect(() => {
    const r = renderers.current[mode];
    if (!r || !active) return;
    const last = seen.current.get(r);
    const restyle = last?.palette !== palette || last.t !== t;
    if (restyle) r.setLook({ palette, t });
    if (restyle || last?.graph !== drawn || last.minConf !== minConf) r.setData(drawn, { minConf });
    seen.current.set(r, { graph: drawn, minConf, palette, t });
    r.highlight(shownPaths);
  }, [active, mode, made, drawn, minConf, palette, t, shownPaths]);

  useEffect(() => {
    const r = renderers.current[mode];
    if (!r || !active || !focus || focus.n === focused.current) return;
    focused.current = focus.n;
    r.focus(focus.address);
  }, [active, mode, made, focus]);

  // The legend gives its room to the side panel on a narrow graph, and comes back with it.
  const [roomy, setRoomy] = useState(true);
  useEffect(() => {
    const el = frame.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => setRoomy(el.clientWidth >= 900));
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  const [legendPref, setLegendPref] = useState<boolean | null>(null); // null: open unless the screen is narrow
  const [legendWith, setLegendWith] = useState<PanelState | null>(null); // the panel it was opened beside
  const legendOpen = (legendPref ?? !narrow) && (!panel || roomy || legendWith === panel);
  const toggleLegend = (open: boolean) => {
    if (open === legendOpen) return; // the toggle event of a change made here
    setLegendPref(open);
    setLegendWith(open ? panel : null);
  };

  return (
    <div
      ref={frame}
      className="relative h-[calc(100dvh-14rem)] min-h-[26rem] overflow-hidden rounded-xl border border-card-border bg-[radial-gradient(75%_65%_at_50%_42%,var(--card-bg)_0%,var(--background)_55%,var(--surface-inset)_100%)]"
    >
      <div ref={canvas} role="img" aria-label={t("tracker-graph:canvas")} className="absolute inset-0" />
      <Legend open={legendOpen} onToggle={toggleLegend} />
      <div className="absolute bottom-3 left-3 right-3 z-10 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-2xl border border-card-border bg-card-bg/95 py-2 pl-2 pr-4 text-sm text-foreground shadow-lg backdrop-blur sm:right-auto">
        <button
          type="button"
          aria-pressed={mode === "3d"}
          aria-label={t("tracker-graph:mode.label")}
          title={noWebGL ? t("tracker-graph:mode.unavailable") : t("tracker-graph:mode.title")}
          disabled={noWebGL}
          onClick={() => setChosen(mode === "3d" ? "2d" : "3d")}
          className={`inline-flex min-h-9 gap-0.5 rounded-lg border border-card-border bg-surface-inset p-0.5 font-mono text-xs font-semibold disabled:opacity-60 ${FOCUS}`}
        >
          {(["3d", "2d"] as const).map((m) => (
            <span key={m} className={`flex items-center rounded-md px-3 ${mode === m ? "bg-foreground text-background" : "text-muted"}`}>
              {m.toUpperCase()}
            </span>
          ))}
        </button>
        <label className="flex items-center gap-2 text-muted">
          {t("tracker-graph:minconf")}
          <output className="w-9 font-mono text-xs tabular-nums text-foreground">{Math.round(minConf * 100)}%</output>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={Math.round(minConf * 100)}
            onChange={(e) => setMinConf(Number(e.target.value) / 100)}
            className={`w-28 cursor-pointer accent-bitcoin ${FOCUS}`}
          />
        </label>
        <label className="flex items-center gap-2 text-muted">
          <input type="checkbox" checked={spam} onChange={(e) => setSpam(e.target.checked)} className={`size-4 accent-bitcoin ${FOCUS}`} />
          {t("tracker-graph:spam")}
        </label>
      </div>
      <div ref={tip} hidden className="pointer-events-none absolute z-20 max-w-[80%] rounded-full bg-foreground px-3.5 py-1.5 text-sm text-background shadow-lg" />
    </div>
  );
}
