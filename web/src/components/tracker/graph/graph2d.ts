// Flat wallet graph (Cytoscape): the fallback for phones, for browsers without WebGL and
// for anyone who prefers it. Same interface as graph3d.ts, same coins and line styles.
import cytoscape from "cytoscape";
import { PATH, chainOf, kindColor, kindOf, kindShape, linkColor, linkDash, pct, title, typeOf } from "@/lib/tracker/kinds";
import type { Graph, GraphNode } from "@/lib/tracker/types";
import { canvasFonts, fadeIn, glyphUrl, reducedMotion, type Look, type Renderer, type RendererOptions } from "./draw";

const COSE_MAX = 250; // nodes; cose is quadratic per pass and freezes the page far beyond this
const HOP = 320; // ms between the hops of a path as it draws itself
const PATH_WIDTH = 6; // the selected path at full zoom, in pixels
const HOP_FONT = 12; // and its hop numbers
const NAME = 11; // my wallets' names at full zoom

function sheet({ palette: p }: Look): cytoscape.StylesheetJson {
  const { sans, mono } = canvasFonts();
  const outline = { "text-outline-color": p.background, "text-outline-width": 2, "min-zoomed-font-size": 7 }; // labels vanish instead of piling up when zoomed out
  // Cytoscape's typings miss data() mappers on several properties: it checks the sheet at run time.
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        color: p.muted,
        "font-family": sans,
        "font-size": 10,
        "text-valign": "bottom",
        "text-margin-y": 5,
        ...outline,
        // The node is its glyph: a coin for my wallets, a shape per type for the rest.
        shape: "ellipse",
        "background-opacity": 0,
        "background-image": "data(image)",
        "background-fit": "contain",
        "background-clip": "none",
        width: "data(size)",
        height: "data(size)",
      },
    },
    // My wallets keep their names at any zoom.
    { selector: "node[?mine]", style: { color: p.foreground, "font-weight": 700, "font-size": "data(font)", "min-zoomed-font-size": 0, "text-transform": "uppercase" } },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        width: "data(width)",
        "line-color": p.muted,
        "target-arrow-color": p.muted,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        opacity: 0.45,
      },
    },
    {
      selector: "edge[kind]",
      style: {
        "line-style": "dashed",
        "line-dash-pattern": "data(dash)",
        "line-color": "data(color)",
        "target-arrow-shape": "none",
        opacity: "data(opacity)",
        label: "data(label)",
        color: "data(color)",
        "font-family": mono,
        "font-size": 9,
        "text-rotation": "autorotate",
        ...outline,
      },
    },
    { selector: "node:selected", style: { "underlay-color": PATH, "underlay-opacity": 0.35, "underlay-padding": 7, "underlay-shape": "ellipse" } },
    { selector: ".faded", style: { opacity: 0.12 } },
    {
      selector: "edge.path",
      style: {
        "line-style": "solid",
        "line-color": PATH,
        "target-arrow-color": PATH,
        "target-arrow-shape": "triangle",
        "source-arrow-shape": "none",
        "arrow-scale": 1.1,
        width: "data(thick)",
        opacity: 1,
        // The hop number, as in the path list: paper on an ink pill with an orange edge.
        label: "data(hop)",
        color: p.cardBg,
        "font-family": mono,
        "font-size": "data(hopFont)",
        "text-rotation": "none",
        "text-background-color": p.foreground,
        "text-background-opacity": 1,
        "text-background-shape": "roundrectangle",
        "text-background-padding": 4,
        "text-border-color": PATH,
        "text-border-width": 2,
        "text-border-opacity": 1,
        "text-outline-width": 0,
      },
    },
  ] as unknown as cytoscape.StylesheetJson;
}

export function create2d(el: HTMLElement, { onSelect }: RendererOptions): Renderer {
  const box = el.appendChild(document.createElement("div"));
  box.style.cssText = "position:absolute;inset:0";
  const cy = cytoscape({ container: box, maxZoom: 2 });
  let look: Look | null = null;
  let restyle = false; // the look changed since the sheet was last set
  // The names of my wallets stay readable when the whole graph is in view.
  const names = () => cy.nodes("[?mine]").data("font", Math.max(NAME, (NAME + 1) / cy.zoom()));
  // So does the selected path: its line and hop numbers never thin out below a readable size.
  const pathStyle = () => {
    const far = Math.max(1, 1 / cy.zoom());
    cy.edges("[hop]").data({ thick: PATH_WIDTH * far, hopFont: HOP_FONT * far });
  };
  cy.on("zoom", () => {
    names();
    pathStyle();
  });
  cy.on("tap", "node", (e) => onSelect(e.target.data("node") as GraphNode));
  cy.on("tap", (e) => {
    if (e.target === cy) onSelect(null);
  });

  // The layout for big graphs. Every address sits in a spiral around its busiest neighbour, and
  // the busiest of all stand on one ring: a wallet and its counterparties read as a constellation.
  function constellations() {
    const SPACING = 30;
    const GOLDEN = Math.PI * (3 - Math.sqrt(5));
    const nodes = cy.nodes().sort((a, b) => b.degree(false) - a.degree(false));
    const rank = new Map(nodes.map((n, i) => [n.id(), i]));
    const children = new Map<string, cytoscape.NodeSingular[]>(nodes.map((n) => [n.id(), []]));
    const roots: cytoscape.NodeSingular[] = [];
    nodes.forEach((n) => {
      const hub = n.neighborhood("node").min((m) => rank.get(m.id()) ?? Infinity).ele as cytoscape.NodeSingular | undefined;
      if (hub && (rank.get(hub.id()) ?? Infinity) < (rank.get(n.id()) ?? Infinity)) children.get(hub.id())?.push(n);
      else roots.push(n);
    });
    const kids = (n: cytoscape.NodeSingular) => children.get(n.id()) ?? [];
    const radius = (n: cytoscape.NodeSingular) => SPACING * Math.sqrt(kids(n).length + 1);
    const span = (n: cytoscape.NodeSingular) => radius(n) + Math.max(0, ...kids(n).map(radius));
    const spread = (n: cytoscape.NodeSingular, x: number, y: number) => {
      n.position({ x, y });
      // The busier a neighbour, the further out it sits, with room for a spiral of its own.
      const around = kids(n).sort((a, b) => radius(a) - radius(b));
      let r = SPACING * 1.5;
      around.forEach((child, k) => {
        r = Math.max(r, SPACING * Math.sqrt(k + 1)) + (radius(child) > SPACING ? radius(child) : 0);
        spread(child, x + r * Math.cos(k * GOLDEN), y + r * Math.sin(k * GOLDEN));
      });
    };
    // The ring is stretched to the shape of the screen.
    const ring = roots.length > 1 ? roots.reduce((sum, n) => sum + span(n) + SPACING, 0) / Math.PI : 0;
    const aspect = Math.min(Math.max(cy.width() / Math.max(cy.height(), 1), 0.5), 2);
    let turn = -Math.PI / 2;
    for (const n of roots) {
      const half = (span(n) + SPACING) / Math.max(ring, 1);
      turn += half;
      spread(n, ring * Math.max(aspect, 1) * Math.cos(turn), (ring / Math.min(aspect, 1)) * Math.sin(turn));
      turn += half;
    }
    cy.fit(undefined, 40);
  }

  function setData(graph: Graph, { minConf }: { minConf: number }) {
    if (!look) return;
    const { palette: p, t } = look;
    const big = graph.nodes.length > COSE_MAX ? 2.4 : 1; // a big graph is seen from afar
    const els: cytoscape.ElementDefinition[] = graph.nodes.map((n) => ({
      group: "nodes",
      data: {
        id: n.address,
        label: (n.category ? `${title(n)} · ${typeOf(n, t)}` : title(n)) + (n.folded ? ` · +${n.folded}` : ""), // +N counterparties folded into it
        image: glyphUrl(kindShape(kindOf(n)), kindColor(kindOf(n), p), p, !n.mine && !n.synced_at && !n.category, chainOf(n)), // dim: not expanded yet
        size: n.mine ? 40 * big : n.category ? 24 * big : 16,
        font: NAME,
        mine: Boolean(n.mine),
        node: n,
      },
    }));
    for (const e of graph.edges)
      els.push({
        group: "edges",
        data: { id: `t|${e.chain}|${e.src}|${e.dst}|${e.token}`, source: e.src, target: e.dst, width: 1 + Math.min(3, Math.log2(e.count) / 2) },
      });
    for (const k of graph.links)
      if (k.confidence >= minConf)
        els.push({
          group: "edges",
          data: {
            id: `l|${k.a}|${k.b}|${k.kind}`,
            source: k.a,
            target: k.b,
            kind: k.kind,
            width: 1.5,
            color: linkColor(k.kind, p),
            dash: linkDash(k.kind),
            opacity: 0.45 + 0.55 * k.confidence,
            label: `${k.kind} ${pct(k.confidence)}`,
          },
        });
    const positions = new Map(cy.nodes().map((n) => [n.id(), { ...n.position() }]));
    const grew = graph.nodes.some((n) => !positions.has(n.address));
    if (restyle) cy.style(sheet(look));
    restyle = false;
    cy.elements().remove();
    cy.add(els);
    cy.nodes().forEach((n) => {
      const at = positions.get(n.id());
      if (at) n.position(at);
    });
    names();
    // Re-run the layout only when nodes appeared, so the picture stays put otherwise.
    if (!grew) return;
    if (cy.nodes().length <= COSE_MAX)
      cy.layout({ name: "cose", animate: false, nodeRepulsion: () => 60000, idealEdgeLength: () => 130, componentSpacing: 80, padding: 40 } as cytoscape.CoseLayoutOptions).run();
    else constellations();
  }

  // The camera glides to what it should show, unless motion is unwanted.
  function frame(eles: cytoscape.CollectionArgument) {
    cy.stop();
    if (reducedMotion()) cy.fit(eles, 60);
    else cy.animate({ fit: { eles, padding: 60 } }, { duration: 500, easing: "ease-in-out-cubic" });
  }

  let timers: ReturnType<typeof setTimeout>[] = [];
  function highlight(paths: string[][]) {
    timers.forEach(clearTimeout);
    timers = [];
    cy.elements().removeClass("faded path").removeData("hop thick hopFont");
    if (paths.flat().length < 2) return;
    const still = reducedMotion();
    let keep = cy.collection();
    for (const path of paths) path.forEach((a, i) => {
      keep = keep.union(cy.getElementById(a));
      if (!i) return;
      // The path draws itself hop by hop.
      const hop = cy.getElementById(a).edgesWith(cy.getElementById(path[i - 1] ?? ""));
      hop.data({ hop: String(i), thick: PATH_WIDTH, hopFont: HOP_FONT });
      if (still) hop.addClass("path");
      else timers.push(setTimeout(() => hop.addClass("path"), (i - 1) * HOP));
      keep = keep.union(hop);
    });
    cy.elements().difference(keep).addClass("faded");
    frame(keep);
    pathStyle(); // the camera may already be where it is sent: no zoom event would follow
  }

  return {
    setLook(next) {
      look = next;
      restyle = true;
    },
    setData,
    highlight,
    focus: (address) => frame(cy.getElementById(address).closedNeighborhood()),
    setActive(on) {
      if (on && box.hidden) fadeIn(box);
      box.hidden = !on;
      if (on) cy.resize();
    },
    destroy() {
      timers.forEach(clearTimeout);
      cy.destroy();
      box.remove();
    },
  };
}
