"use strict";

// Colour and shape per node kind; shape repeats the colour for colour-blind readers.
const KINDS = {
  mine: ["#3fb950", "ellipse", "My wallet (private)"],
  public: ["#f0883e", "ellipse", "My wallet (public)"],
  cex: ["#f85149", "round-rectangle", "Exchange (CEX)"],
  dex: ["#a371f7", "diamond", "DEX / swap"],
  bridge: ["#58a6ff", "hexagon", "Bridge"],
  mixer: ["#e3b341", "triangle", "Mixer"],
  defi: ["#39c5cf", "round-diamond", "DeFi"],
  service: ["#db61a2", "round-rectangle", "Other service"],
  other: ["#b1bac4", "round-tag", "Labelled, other"],
  external: ["#6e7681", "ellipse", "Unknown external"],
};
const LINKS = { "common-input": "#e3b341", change: "#39c5cf", bridge: "#58a6ff" };
const EXPLORER = {
  bitcoin: "https://mempool.space",
  ethereum: "https://eth.blockscout.com",
  optimism: "https://explorer.optimism.io",
};

const $ = (id) => document.getElementById(id);
// All text reaches the DOM as text nodes: labels and token names come from strangers.
function h(tag, props, ...children) {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...children.filter((c) => c != null));
  return el;
}
const short = (a) => (a.length > 14 ? `${a.slice(0, 7)}…${a.slice(-5)}` : a);
const num = (x) => Number(x).toLocaleString("en", { maximumSignificantDigits: 5 });
const pct = (c) => `${Math.round(c * 100)}%`;
const day = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "?");

async function api(path, body, method) {
  const r = await fetch(`api/${path}`, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === "string" ? detail : `${r.status} ${r.statusText}`);
  }
  return r.json();
}
const fail = (e) => {
  $("status").textContent = e.message;
  $("status").className = "error";
};

const cy = cytoscape({
  container: $("cy"),
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

let graph = { nodes: [], edges: [], links: [] };

function kindOf(n) {
  if (n.mine) return n.public ? "public" : "mine";
  return n.category || "external";
}

function draw() {
  const minConf = $("minconf").value / 100;
  const els = [];
  for (const n of graph.nodes) {
    const [color, shape] = KINDS[kindOf(n)];
    els.push({
      group: "nodes",
      data: {
        id: n.address,
        label: n.name || n.label || short(n.address),
        color,
        shape,
        size: n.mine ? 34 : n.category ? 26 : 18,
        isPublic: n.mine && n.public,
        unsynced: !n.mine && !n.synced_at && !n.category,
        node: n,
      },
    });
  }
  for (const e of graph.edges) {
    els.push({
      group: "edges",
      data: {
        id: `t|${e.chain}|${e.src}|${e.dst}|${e.token}`,
        source: e.src,
        target: e.dst,
        width: 1 + Math.min(4, Math.log2(e.count)),
        edge: e,
      },
    });
  }
  for (const k of graph.links) {
    if (k.confidence < minConf) continue;
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
        link: k,
      },
    });
  }
  const known = new Set(cy.nodes().map((n) => n.id()));
  const grew = graph.nodes.some((n) => !known.has(n.address));
  const positions = Object.fromEntries(cy.nodes().map((n) => [n.id(), n.position()]));
  cy.elements().remove();
  cy.add(els);
  cy.nodes().forEach((n) => positions[n.id()] && n.position(positions[n.id()]));
  // Re-run the layout only when nodes appeared, so the picture stays put otherwise.
  if (grew) cy.layout({ name: "cose", animate: false, nodeRepulsion: () => 60000, idealEdgeLength: () => 130, componentSpacing: 80, padding: 40 }).run();
}

function renderWallets() {
  $("wallets").replaceChildren(
    ...graph.nodes
      .filter((n) => n.mine)
      .map((n) =>
        h(
          "li",
          {},
          h(
            "button",
            { className: "wallet", onclick: () => select(n.address) },
            n.name || short(n.address),
            n.public ? h("span", { className: "badge public" }, "public") : null,
            n.error ? h("span", { className: "badge error", title: n.error }, "sync error") : null
          )
        )
      )
  );
}

function renderTraces(traces) {
  const name = (a) => {
    const n = graph.nodes.find((x) => x.address === a);
    return (n && (n.name || n.label)) || short(a);
  };
  const items = traces.map((t) => {
    const how = t.path.length
      ? `${pct(t.confidence)} · ${t.path.length - 1} hop${t.path.length > 2 ? "s" : ""} · ${[...new Set(t.kinds)].join(", ")}`
      : "no on-chain path";
    const via = t.shared_services.length ? ` · both used ${t.shared_services.join(", ")}` : "";
    const button = h(
      "button",
      { className: "trace", ariaPressed: "false" },
      `${name(t.a)} ↔ ${name(t.b)}`,
      t.exposure && t.path.length ? h("span", { className: "badge exposed" }, "exposed") : null,
      h("span", { className: "hint" }, how + via)
    );
    button.onclick = () => {
      const on = button.ariaPressed !== "true";
      document.querySelectorAll(".trace").forEach((b) => (b.ariaPressed = "false"));
      button.ariaPressed = String(on);
      highlight(on ? t.path : []);
    };
    return h("li", {}, button);
  });
  $("traces").replaceChildren(...(items.length ? items : [h("li", { className: "hint" }, "No link found between your wallets.")]));
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
  cy.fit(keep, 80);
}

function select(address) {
  const node = cy.getElementById(address);
  cy.elements().unselect();
  node.select();
  cy.center(node);
}

function close() {
  $("detail").hidden = true;
}

function row(dl, term, value) {
  if (value != null && value !== "") dl.append(h("dt", {}, term), h("dd", {}, value));
}

function showNode(n) {
  const act = (fn) => () => fn().then(refresh).catch(fail);
  const dl = h("dl", {});
  row(dl, "Type", KINDS[kindOf(n)][2]);
  row(dl, "Label", n.label);
  row(dl, "Synced", n.synced_at ? day(n.synced_at) : "not yet — its own counterparties are unknown");
  row(dl, "Note", n.hub ? "very active address: only its newest transactions are loaded, and traces do not pass through it" : "");
  row(dl, "Error", n.error);
  for (const b of n.balances || []) row(dl, `${b.asset} · ${b.chain}`, num(b.amount));
  const chain = n.address.startsWith("0x") ? "ethereum" : "bitcoin";
  const label = h("form", {},
    h("label", {}, "Label ", h("input", { name: "name", value: n.label || "", maxLength: 80, placeholder: "e.g. Kraken deposit" })),
    h("label", {}, "Type ", h("select", { name: "category" }, ...["cex", "dex", "bridge", "mixer", "defi", "service", "other"].map((c) => h("option", { value: c, selected: c === (n.category || "other") }, KINDS[c][2])))),
    h("button", {}, "Save label")
  );
  label.onsubmit = (e) => {
    e.preventDefault();
    act(() => api("labels", { address: n.address, name: label.elements["name"].value, category: label.elements["category"].value }))();
  };
  const wallet = (pub) => act(() => api("wallets", { address: n.address, name: n.name, public: pub }));
  $("detail").replaceChildren(
    h("h2", {}, n.name || n.label || "Address"),
    h("div", { className: "mono" }, n.address),
    dl,
    h("div", { className: "buttons" },
      h("button", { onclick: () => startSync([n.address]) }, n.synced_at ? "Re-sync" : "Expand"),
      n.mine
        ? h("button", { onclick: wallet(!n.public) }, n.public ? "Make private" : "Make public")
        : h("button", { onclick: wallet(false) }, "This is mine"),
      n.mine ? h("button", { onclick: act(() => api(`wallets/${n.address}`, null, "DELETE")) }, "Not mine") : null,
      h("a", { href: `${EXPLORER[chain]}/address/${n.address}`, target: "_blank", rel: "noreferrer noopener" }, "Explorer ↗"),
      h("button", { onclick: close }, "Close")
    ),
    n.mine ? null : label
  );
  $("detail").hidden = false;
}

function showEdge(d) {
  const dl = h("dl", {});
  let title;
  if (d.link) {
    const k = d.link;
    title = `Guess: ${k.kind}`;
    row(dl, "Confidence", pct(k.confidence));
    row(dl, "Meaning", {
      "common-input": "Both addresses signed inputs of the same Bitcoin transaction, so one wallet holds both keys (unless it is a CoinJoin).",
      change: "One address looks like the change output of a payment made by the other.",
      bridge: "A transfer out on one chain matches a transfer in on another: same asset, near-equal amount, shortly after.",
    }[k.kind]);
    row(dl, "Evidence", `${k.count} transaction${k.count > 1 ? "s" : ""}`);
    for (const ev of k.evidence) dl.append(h("dd", { className: "mono" }, ev));
  } else {
    const e = d.edge;
    title = `${num(e.amount)} ${e.asset}`;
    row(dl, "Chain", e.chain);
    row(dl, "From", h("span", { className: "mono" }, e.src));
    row(dl, "To", h("span", { className: "mono" }, e.dst));
    row(dl, "Transfers", String(e.count));
    row(dl, "Period", e.first === e.last ? day(e.first) : `${day(e.first)} → ${day(e.last)}`);
    row(dl, "Token", e.token && h("span", { className: "mono" }, e.token));
    if (EXPLORER[e.chain])
      row(dl, "Example", h("a", { href: `${EXPLORER[e.chain]}/tx/${e.txid}`, target: "_blank", rel: "noreferrer noopener" }, "transaction ↗"));
  }
  $("detail").replaceChildren(h("h2", {}, title), dl, h("div", { className: "buttons" }, h("button", { onclick: close }, "Close")));
  $("detail").hidden = false;
}

cy.on("select", "node", (e) => showNode(e.target.data("node")));
cy.on("select", "edge", (e) => showEdge(e.target.data()));
cy.on("tap", (e) => e.target === cy && close());

async function refresh() {
  graph = await api(`graph?spam=${$("spam").checked}`);
  draw();
  renderWallets();
  renderTraces(await api("trace"));
  const selected = cy.$("node:selected");
  if (!$("detail").hidden && selected.length) showNode(selected.data("node"));
}

async function poll() {
  const s = await api("sync");
  $("sync").disabled = s.running;
  $("status").className = s.error ? "error" : "";
  $("status").textContent = s.running
    ? `Syncing ${s.done}/${s.total} ${s.current === "labels" ? "(downloading labels)" : short(s.current)}`
    : s.error || (s.total ? `Synced ${s.done} address${s.done === 1 ? "" : "es"}.` : "");
  if (s.running) setTimeout(() => poll().catch(fail), 1500);
  else if (s.total) await refresh();
}

function startSync(addresses) {
  api("sync", { addresses, depth: addresses.length ? 0 : Number($("depth").value), labels: !addresses.length && $("labels").checked })
    .then(poll)
    .catch(fail);
}

$("add").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  api("wallets", { address: f.address.value, name: f.elements["name"].value, public: f.public.checked })
    .then(() => {
      f.reset();
      $("status").textContent = "";
      return refresh();
    })
    .catch(fail);
};
$("sync").onclick = () => startSync([]);
$("spam").onchange = () => refresh().catch(fail);
$("minconf").oninput = () => {
  $("minout").textContent = `${$("minconf").value}%`;
  draw();
};
$("menu").onclick = () => $("menu").setAttribute("aria-expanded", String(!$("panel").classList.toggle("closed")));
$("legend").replaceChildren(
  ...Object.values(KINDS).map(([color, , text]) => h("li", {}, h("span", { className: "swatch", style: `background:${color}` }), text)),
  ...Object.entries(LINKS).map(([kind, color]) => h("li", {}, h("span", { className: "swatch", style: `background:none;border-top:2px dashed ${color};border-radius:0;height:2px` }), `${kind} (guess)`))
);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
refresh().then(poll).catch(fail);
