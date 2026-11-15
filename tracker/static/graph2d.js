// Flat wallet graph (Cytoscape): the fallback for phones, for browsers without WebGL and
// for anyone who prefers it. Same interface as graph3d.js.
import { KINDS, LINKS, kindOf, pct, title } from "./kinds.js";

export function create2d(el, { onSelect }) {
  const box = el.appendChild(document.createElement("div"));
  box.style.cssText = "position:absolute;inset:0";
  const cy = cytoscape({
    container: box,
    maxZoom: 2,
    style: [
      {
        selector: "node",
        style: {
          label: "data(label)",
          color: "#e6edf3",
          "font-size": 10,
          "min-zoomed-font-size": 7, // labels vanish instead of piling up when zoomed out
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-outline-color": "#0d1117",
          "text-outline-width": 2,
          "background-color": "data(color)",
          shape: "data(shape)",
          width: "data(size)",
          height: "data(size)",
        },
      },
      { selector: "node[?unsynced]", style: { "background-opacity": 0.45 } },
      { selector: "node[?isPublic]", style: { "border-width": 4, "border-color": "#fff" } },
      {
        selector: "edge",
        style: {
          "curve-style": "bezier",
          width: "data(width)",
          "line-color": "#484f58",
          "target-arrow-color": "#484f58",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.8,
        },
      },
      {
        selector: "edge[kind]",
        style: {
          "line-style": "dashed",
          "line-color": "data(color)",
          "target-arrow-shape": "none",
          opacity: "data(opacity)",
          label: "data(label)",
          color: "data(color)",
          "font-size": 9,
          "min-zoomed-font-size": 7,
          "text-rotation": "autorotate",
          "text-outline-color": "#0d1117",
          "text-outline-width": 2,
        },
      },
      { selector: ":selected", style: { "overlay-color": "#58a6ff", "overlay-opacity": 0.25 } },
      { selector: ".faded", style: { opacity: 0.08 } },
      { selector: "edge.path", style: { "line-color": "#f0883e", "target-arrow-color": "#f0883e", width: 4, opacity: 1 } },
    ],
  });
  cy.on("tap", "node", (e) => onSelect(e.target.data("node")));
  cy.on("tap", (e) => e.target === cy && onSelect(null));

  function setData(graph, { minConf }) {
    const els = graph.nodes.map((n) => ({
      group: "nodes",
      data: {
        id: n.address,
        label: n.category ? `${title(n)} · ${KINDS[kindOf(n)][2]}` : title(n),
        color: KINDS[kindOf(n)][0],
        shape: KINDS[kindOf(n)][1],
        size: n.mine ? 34 : n.category ? 26 : 18,
        isPublic: n.mine && n.public,
        unsynced: !n.mine && !n.synced_at && !n.category,
        node: n,
      },
    }));
    for (const e of graph.edges)
      els.push({
        group: "edges",
        data: { id: `t|${e.chain}|${e.src}|${e.dst}|${e.token}`, source: e.src, target: e.dst, width: 1 + Math.min(4, Math.log2(e.count)) },
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
            width: 2,
            color: LINKS[k.kind] || "#fff",
            opacity: 0.35 + 0.65 * k.confidence,
            label: `${k.kind} ${pct(k.confidence)}`,
          },
        });
    const positions = Object.fromEntries(cy.nodes().map((n) => [n.id(), n.position()]));
    const grew = graph.nodes.some((n) => !positions[n.address]);
    cy.elements().remove();
    cy.add(els);
    cy.nodes().forEach((n) => positions[n.id()] && n.position(positions[n.id()]));
    // Re-run the layout only when nodes appeared, so the picture stays put otherwise.
    if (grew) cy.layout({ name: "cose", animate: false, nodeRepulsion: () => 60000, idealEdgeLength: () => 130, componentSpacing: 80, padding: 40 }).run();
  }

  function highlight(path) {
    cy.elements().removeClass("faded path");
    if (path.length < 2) return;
    let keep = cy.collection();
    path.forEach((a, i) => {
      keep = keep.union(cy.getElementById(a));
      if (i) keep = keep.union(cy.getElementById(a).edgesWith(cy.getElementById(path[i - 1])).addClass("path"));
    });
    cy.elements().difference(keep).addClass("faded");
    cy.fit(keep, 60);
  }

  return {
    setData,
    highlight,
    focus: (address) => cy.fit(cy.getElementById(address).closedNeighborhood(), 60),
    setActive(on) {
      box.hidden = !on;
      if (on) cy.resize();
    },
  };
}
