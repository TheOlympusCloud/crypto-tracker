// Flat wallet graph (Cytoscape): the fallback for phones, for browsers without WebGL and
// for anyone who prefers it. Same interface as graph3d.js, same coins and line styles.
import { KINDS, PAPER, PATH, glyphUrl, kindOf, linkColor, linkDash, pct, title } from "./kinds.js";

const COSE_MAX = 250; // nodes; cose is quadratic per pass and freezes the page far beyond this

const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const HOP = 320; // ms between the hops of a path as it draws itself
const PATH_WIDTH = 6; // the selected path at full zoom, in pixels
const HOP_FONT = 12; // and its hop numbers

export function create2d(el, { onSelect }) {
  const box = el.appendChild(document.createElement("div"));
  box.style.cssText = "position:absolute;inset:0";
  const outline = { "text-outline-color": PAPER.page, "text-outline-width": 2, "min-zoomed-font-size": 7 }; // labels vanish instead of piling up when zoomed out
  const cy = cytoscape({
    container: box,
    maxZoom: 2,
    style: [
      {
        selector: "node",
        style: {
          label: "data(label)",
          color: PAPER.quiet,
          "font-family": "Red Hat Text, system-ui, sans-serif",
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
      { selector: "node[?mine]", style: { color: PAPER.ink, "font-family": "Red Hat Display, sans-serif", "font-weight": 900, "font-size": "data(font)", "min-zoomed-font-size": 0, "text-transform": "uppercase" } },
      {
        selector: "edge",
        style: {
          "curve-style": "bezier",
          width: "data(width)",
          "line-color": PAPER.quiet,
          "target-arrow-color": PAPER.quiet,
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
          "font-family": "JetBrains Mono, monospace",
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
          color: PAPER.card,
          "font-family": "JetBrains Mono, monospace",
          "font-size": "data(hopFont)",
          "text-rotation": "none",
          "text-background-color": PAPER.ink,
          "text-background-opacity": 1,
          "text-background-shape": "roundrectangle",
          "text-background-padding": 4,
          "text-border-color": PATH,
          "text-border-width": 2,
          "text-border-opacity": 1,
          "text-outline-width": 0,
        },
      },
    ],
  });
  // The names of my wallets stay readable when the whole graph is in view.
  const NAME = 11;
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
  cy.on("tap", "node", (e) => onSelect(e.target.data("node")));
  cy.on("tap", (e) => e.target === cy && onSelect(null));

  // The layout for big graphs. Every address sits in a spiral around its busiest neighbour, and
  // the busiest of all stand on one ring: a wallet and its counterparties read as a constellation.
  function constellations() {
    const SPACING = 30;
    const GOLDEN = Math.PI * (3 - Math.sqrt(5));
    const nodes = cy.nodes().sort((a, b) => b.degree() - a.degree());
    const rank = new Map(nodes.map((n, i) => [n.id(), i]));
    const children = new Map(nodes.map((n) => [n.id(), []]));
    const roots = [];
    nodes.forEach((n) => {
      const hub = n.neighborhood("node").min((m) => rank.get(m.id())).ele;
      if (hub && rank.get(hub.id()) < rank.get(n.id())) children.get(hub.id()).push(n);
      else roots.push(n);
    });
    const radius = (n) => SPACING * Math.sqrt(children.get(n.id()).length + 1);
    const span = (n) => radius(n) + Math.max(0, ...children.get(n.id()).map(radius));
    const spread = (n, x, y) => {
      n.position({ x, y });
      // The busier a neighbour, the further out it sits, with room for a spiral of its own.
      const around = children.get(n.id()).sort((a, b) => radius(a) - radius(b));
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

  function setData(graph, { minConf }) {
    const big = graph.nodes.length > COSE_MAX ? 2.4 : 1; // a big graph is seen from afar
    const els = graph.nodes.map((n) => {
      const [color, shape, word] = KINDS[kindOf(n)];
      return {
        group: "nodes",
        data: {
          id: n.address,
          label: n.category ? `${title(n)} · ${word}` : title(n),
          image: glyphUrl(shape, color, !n.mine && !n.synced_at && !n.category), // dim: not expanded yet
          size: n.mine ? 40 * big : n.category ? 24 * big : 16,
          font: NAME,
          mine: Boolean(n.mine),
          node: n,
        },
      };
    });
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
            color: linkColor(k.kind),
            dash: linkDash(k.kind),
            opacity: 0.45 + 0.55 * k.confidence,
            label: `${k.kind} ${pct(k.confidence)}`,
          },
        });
    const positions = Object.fromEntries(cy.nodes().map((n) => [n.id(), n.position()]));
    const grew = graph.nodes.some((n) => !positions[n.address]);
    cy.elements().remove();
    cy.add(els);
    cy.nodes().forEach((n) => positions[n.id()] && n.position(positions[n.id()]));
    // Re-run the layout only when nodes appeared, so the picture stays put otherwise.
    names();
    if (!grew) return;
    if (cy.nodes().length <= COSE_MAX) cy.layout({ name: "cose", animate: false, nodeRepulsion: () => 60000, idealEdgeLength: () => 130, componentSpacing: 80, padding: 40 }).run();
    else constellations();
  }

  // The camera glides to what it should show, unless motion is unwanted.
  function frame(eles) {
    cy.stop();
    if (still) cy.fit(eles, 60);
    else cy.animate({ fit: { eles, padding: 60 } }, { duration: 500, easing: "ease-in-out-cubic" });
  }

  let timers = [];
  function highlight(path) {
    timers.forEach(clearTimeout);
    timers = [];
    cy.elements().removeClass("faded path").removeData("hop thick hopFont");
    if (path.length < 2) return;
    let keep = cy.collection();
    path.forEach((a, i) => {
      keep = keep.union(cy.getElementById(a));
      if (!i) return;
      // The path draws itself hop by hop.
      const hop = cy.getElementById(a).edgesWith(cy.getElementById(path[i - 1]));
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
    setData,
    highlight,
    focus: (address) => frame(cy.getElementById(address).closedNeighborhood()),
    setActive(on) {
      box.hidden = !on;
      if (on) cy.resize();
    },
  };
}
