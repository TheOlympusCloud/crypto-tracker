import { create2d } from "./graph2d.js";
import { KINDS, LINKS, kindOf, pct, short, sourceKind, title } from "./kinds.js";

const EXPLORER = { bitcoin: "https://mempool.space", ethereum: "https://eth.blockscout.com" };
const $ = (id) => document.getElementById(id);
// All text reaches the DOM as text nodes: labels and token names come from strangers.
function h(tag, props, ...children) {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...children.flat().filter((c) => c != null && c !== false));
  return el;
}
const num = (x) => Number(x).toLocaleString("en", { maximumSignificantDigits: 5 });
const day = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "?");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const badge = (n) => h("span", { className: `badge ${n.public ? "public" : "private"}` }, n.public ? "public" : "private");

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
const act = (fn) => () => fn().then(refresh).catch(fail);

let graph = { nodes: [], edges: [], links: [] };
let analysis = { verdicts: [], origins: {}, max_hops: 4 };
let byAddress = new Map();
let shownPath = [];
const nameOf = (a) => (byAddress.has(a) ? title(byAddress.get(a)) : short(a));

// ---------- graph view: 3D where it works, flat otherwise ----------

const renderers = {};
let mode = matchMedia("(max-width: 760px)").matches ? "2d" : "3d";
let stale = true; // the active renderer has not seen the current data

async function renderer() {
  if (!renderers[mode]) {
    try {
      renderers[mode] = mode === "3d" ? (await import("./graph3d.js")).create3d($("canvas"), { onSelect }) : create2d($("canvas"), { onSelect });
    } catch {
      mode = "2d"; // no WebGL here
      return renderer();
    }
  }
  return renderers[mode];
}

async function drawGraph() {
  if ($("view-graph").hidden) return;
  const active = await renderer();
  for (const [name, r] of Object.entries(renderers)) r.setActive(name === mode);
  $("mode").textContent = mode === "3d" ? "3D" : "2D";
  $("mode").ariaPressed = String(mode === "3d");
  if (stale) active.setData(graph, { minConf: $("minconf").value / 100 });
  stale = false;
  active.highlight(shownPath);
}

function show(view) {
  for (const b of document.querySelectorAll("nav button")) {
    b.ariaCurrent = b.dataset.view === view ? "page" : null;
    $(`view-${b.dataset.view}`).hidden = b.dataset.view !== view;
  }
  if (view !== "graph") Object.values(renderers).forEach((r) => r.setActive(false));
  return drawGraph().catch(fail);
}

// ---------- portfolio ----------

function renderPortfolio() {
  const mine = graph.nodes.filter((n) => n.mine);
  const assets = new Map(); // symbol -> totals
  for (const n of mine)
    for (const b of n.balances || []) {
      const a = assets.get(b.asset) || { asset: b.asset, private: 0, public: 0, chains: new Set() };
      a[n.public ? "public" : "private"] += b.amount;
      a.chains.add(b.chain);
      assets.set(b.asset, a);
    }
  const rows = [...assets.values()].sort((a, b) => a.asset.localeCompare(b.asset));
  const side = (key, text, hint) => {
    const wallets = mine.filter((n) => Boolean(n.public) === (key === "public"));
    return h(
      "div",
      { className: `card split ${key}` },
      h("h2", {}, text, h("span", { className: "count" }, plural(wallets.length, "wallet"))),
      h("p", { className: "hint" }, hint),
      h("ul", { className: "amounts" }, rows.filter((a) => a[key]).map((a) => h("li", {}, h("b", {}, num(a[key])), ` ${a.asset}`))),
      rows.some((a) => a[key]) ? null : h("p", { className: "hint" }, "Nothing here yet.")
    );
  };
  $("summary").replaceChildren(
    side("private", "Private", "Wallets nobody should be able to tie to you."),
    side("public", "Public", "Wallets tied to your identity: KYC exchange withdrawals, addresses you shared.")
  );

  $("assets").replaceChildren(
    rows.length
      ? h(
          "table",
          {},
          h("thead", {}, h("tr", {}, ...["Asset", "Total", "Private", "Public", "Split", "Chains"].map((t) => h("th", { scope: "col" }, t)))),
          h(
            "tbody",
            {},
            rows.map((a) => {
              const total = a.private + a.public;
              const share = total ? (a.private / total) * 100 : 0;
              return h(
                "tr",
                {},
                h("th", { scope: "row" }, a.asset),
                h("td", {}, num(total)),
                h("td", {}, num(a.private)),
                h("td", {}, num(a.public)),
                h("td", {}, h("span", { className: "bar", role: "img", ariaLabel: `${Math.round(share)}% private` }, h("i", { style: `width:${share}%;background:${KINDS.mine[0]}` }), h("i", { style: `width:${100 - share}%;background:${KINDS.public[0]}` }))),
                h("td", {}, [...a.chains].join(", "))
              );
            })
          )
        )
      : h("p", { className: "hint" }, mine.length ? "No balances yet: sync your wallets." : "Add your wallets under “Wallets & sync” to start.")
  );

  $("walletcards").replaceChildren(
    ...analysis.verdicts.map((v) => {
      const n = byAddress.get(v.address);
      const verdict = judge(v);
      const origin = (analysis.origins[v.address] || [])[0];
      return h(
        "button",
        { className: "card wallet", onclick: () => openWallet(v.address) },
        h("span", { className: "name" }, title(n), badge(n)),
        h("span", { className: "mono" }, short(v.address)),
        h("span", { className: "amounts" }, (n.balances || []).slice(0, 4).map((b) => h("span", {}, h("b", {}, num(b.amount)), ` ${b.asset}`))),
        h("span", { className: `verdict ${verdict.level}` }, verdict.headline),
        origin ? h("span", { className: "hint" }, `${origin.asset} came from`, originBar(origin)) : null
      );
    })
  );
}

// ---------- verdicts ----------

function describeLink(k) {
  const hops = k.path.length - 1;
  const kinds = [...new Set(k.kinds)].join(" + ");
  const via = k.via.length ? `, through your wallet ${k.via.map(nameOf).join(", ")}` : "";
  return `${nameOf(k.wallet)} (${k.public ? "public" : "private"}): ${pct(k.confidence)} confidence, ${plural(hops, "hop")}, ${kinds}${via}`;
}

// The headline of a wallet's verdict. "ok" never means safe, only that nothing was found.
function judge(v) {
  if (!v.coverage.synced) return { level: "unknown", headline: "Not checked yet: sync this wallet" };
  const exposing = v.links.filter((k) => k.public !== v.public);
  const best = (v.public ? v.links : exposing)[0] || v.links[0];
  if (!best) return { level: "ok", headline: "No link to your other wallets found" };
  if (v.public) return { level: "warn", headline: `Tied to ${plural(v.links.length, "other wallet")} of yours` };
  if (exposing.length) return { level: "bad", headline: `Linked to public wallet ${nameOf(best.wallet)} (${pct(best.confidence)})` };
  return { level: "warn", headline: `Linked to private wallet ${nameOf(best.wallet)} (${pct(best.confidence)})` };
}

// What the verdict cannot rule out, said plainly.
function caveats(v) {
  const c = v.coverage;
  const out = [];
  if (c.error) out.push(`The last sync of this wallet had errors (${c.error}), so its history may be incomplete.`);
  if (c.truncated) out.push("This wallet is very active and only its newest transactions are loaded. Older links are not seen.");
  if (c.counterparties > c.expanded)
    out.push(`${c.counterparties - c.expanded} of its ${c.counterparties} unlabelled ${c.counterparties === 1 ? "counterparty" : "counterparties"} have not been expanded. A link that runs through two or more strangers can hide there: sync deeper, or expand them in the graph.`);
  for (const s of v.shared_services) out.push(`Both this wallet and ${nameOf(s.wallet)} used ${s.services.join(", ")}. The chain does not link them, but that service can.`);
  const exchanges = v.services.filter((s) => s.category === "cex").map((s) => s.name);
  if (exchanges.length && !v.public) out.push(`It dealt directly with ${exchanges.join(", ")}. If that account is in your name, the exchange knows this wallet is yours: consider marking it public.`);
  if (v.services.some((s) => s.category === "mixer")) out.push("It dealt with a mixer. Funds past a mixer cannot be followed here.");
  out.push(`Paths longer than ${plural(analysis.max_hops, "hop")} are not searched.`);
  out.push("Swaps that change the asset, chains that are not configured, unlabelled exchange wallets and anything off-chain (same IP address, same exchange account) are invisible to this check.");
  return out;
}

// ---------- funds origin ----------

function originBar(asset) {
  return h(
    "span",
    { className: "bar", role: "img", ariaLabel: asset.sources.map((s) => `${pct(s.share)} ${KINDS[sourceKind(s)][2]}`).join(", ") },
    asset.sources.map((s) => h("i", { style: `width:${s.share * 100}%;background:${KINDS[sourceKind(s)][0]}`, title: `${pct(s.share)} ${s.name || short(s.address)}` }))
  );
}

function originRows(address) {
  const assets = analysis.origins[address] || [];
  if (!assets.length) return [h("p", { className: "hint" }, "No incoming transfers known.")];
  return assets.map((a) =>
    h(
      "div",
      { className: "origin" },
      h("h4", {}, `${num(a.received)} ${a.asset} received on ${a.chain}`),
      originBar(a),
      h(
        "ul",
        {},
        a.sources.map((s) =>
          h(
            "li",
            {},
            h(
              "button",
              { className: "rowbtn", onclick: () => showPath(s.path, `Origin of ${a.asset} in ${nameOf(address)}`, address) },
              h("span", { className: "swatch", style: `background:${KINDS[sourceKind(s)][0]}` }),
              h("b", {}, pct(s.share)),
              ` ${s.name || short(s.address)} · ${KINDS[sourceKind(s)][2]}`,
              h("span", { className: "hint" }, ` ${num(s.amount)} ${a.asset} · ${plural(s.path.length - 1, "hop")}${s.reason ? ` · followed no further: ${s.reason}` : ""}`)
            )
          )
        ),
        rest(a) > 0.005 ? h("li", { className: "hint" }, `${pct(rest(a))} from smaller sources not listed (the empty end of the bar)`) : null
      )
    )
  );
}
const rest = (asset) => 1 - asset.sources.reduce((sum, s) => sum + s.share, 0);

// ---------- detail panel ----------

function panel(...children) {
  $("detail").replaceChildren(h("button", { className: "close", ariaLabel: "Close details", onclick: closePanel }, "✕"), ...children);
  $("detail").hidden = false;
  document.body.classList.add("with-detail"); // the graph makes room instead of hiding under it
}
function closePanel() {
  $("detail").hidden = true;
  document.body.classList.remove("with-detail");
}

function openWallet(address) {
  const n = byAddress.get(address);
  const v = analysis.verdicts.find((x) => x.address === address);
  if (!n || !v) return;
  const verdict = judge(v);
  const wallet = (pub) => act(() => api("wallets", { address, name: n.name, public: pub }));
  panel(
    h("h2", {}, title(n), badge(n)),
    h("div", { className: "mono" }, address),
    h("ul", { className: "amounts" }, (n.balances || []).map((b) => h("li", {}, h("b", {}, num(b.amount)), ` ${b.asset} · ${b.chain}`))),
    h(
      "div",
      { className: "buttons" },
      h("button", { onclick: () => showPath([], "", address) }, "Show in graph"),
      h("button", { onclick: wallet(!n.public) }, n.public ? "Make private" : "Make public"),
      h("button", { onclick: () => startSync([address]) }, "Re-sync")
    ),
    h("h3", {}, "Can it be tied to your other wallets?"),
    h("p", { className: `verdict ${verdict.level}` }, verdict.headline),
    h("ul", {}, v.links.map((k) => h("li", {}, h("button", { className: "rowbtn", onclick: () => showPath(k.path, `${nameOf(address)} ↔ ${nameOf(k.wallet)}`, address) }, describeLink(k))))),
    h("h4", {}, v.links.length ? "What this check may still have missed" : "This is not proof of privacy"),
    h("ul", { className: "caveats" }, caveats(v).map((c) => h("li", {}, c))),
    h("h3", {}, "Where its funds came from"),
    h("p", { className: "hint" }, "Followed back to a labelled source, one of your wallets, or the edge of what is known. Shares are an estimate: an address that forwards money passes on its own sources in proportion."),
    ...originRows(address)
  );
}

function hopText(a, b) {
  const facts = graph.edges.filter((e) => (e.src === a && e.dst === b) || (e.src === b && e.dst === a));
  const guesses = graph.links.filter((k) => (k.a === a && k.b === b) || (k.a === b && k.b === a));
  return [
    ...facts.map((e) => `${e.src === a ? "sent" : "received"} ${num(e.amount)} ${e.asset} on ${e.chain} (${plural(e.count, "transfer")}, ${e.first === e.last ? day(e.first) : `${day(e.first)} to ${day(e.last)}`})`),
    ...guesses.map((k) => `guess: ${k.kind}, ${pct(k.confidence)} confidence (${plural(k.count, "transaction")})`),
  ];
}

// A path opened from a verdict or an origin: highlighted in the graph, spelled out as a list.
function showPath(path, heading, back) {
  shownPath = path;
  show("graph").then(async () => {
    if (!path.length) (await renderer()).focus(back);
  });
  if (!path.length) return selectNode(byAddress.get(back));
  panel(
    h("h2", {}, heading),
    h("p", { className: "hint" }, `${plural(path.length - 1, "hop")}, highlighted in orange.`),
    h(
      "ol",
      { className: "path" },
      path.map((a, i) =>
        h(
          "li",
          {},
          h("button", { className: "rowbtn", onclick: () => selectNode(byAddress.get(a)) }, h("span", { className: "swatch", style: `background:${KINDS[kindOf(byAddress.get(a) || {})][0]}` }), nameOf(a), h("span", { className: "hint" }, ` ${KINDS[kindOf(byAddress.get(a) || {})][2]}`)),
          i < path.length - 1 ? h("ul", { className: "hint" }, hopText(a, path[i + 1]).map((t) => h("li", {}, t))) : null
        )
      )
    ),
    h("div", { className: "buttons" }, h("button", { onclick: () => openWallet(back) }, `Back to ${nameOf(back)}`), h("button", { onclick: () => showPath([], "", back) }, "Clear highlight"))
  );
}

function selectNode(n) {
  if (!n) return closePanel();
  const chain = n.address.startsWith("0x") ? "ethereum" : "bitcoin";
  const form = h(
    "form",
    {},
    h("label", {}, "Label ", h("input", { name: "label", value: n.label || "", maxLength: 80, placeholder: "e.g. Kraken deposit" })),
    h("label", {}, "Type ", h("select", { name: "category" }, ["cex", "dex", "bridge", "mixer", "defi", "service", "other"].map((c) => h("option", { value: c, selected: c === (n.category || "other") }, KINDS[c][2])))),
    h("button", {}, "Save label")
  );
  form.onsubmit = (e) => {
    e.preventDefault();
    act(() => api("labels", { address: n.address, name: form.elements.label.value, category: form.elements.category.value }))();
  };
  const facts = h("dl", {});
  const row = (term, value) => value && facts.append(h("dt", {}, term), h("dd", {}, value));
  row("Type", KINDS[kindOf(n)][2]);
  row("Label", n.label);
  row("Synced", n.synced_at ? day(n.synced_at) : "not yet, so its own counterparties are unknown");
  row("Note", n.hub && "very active address: only its newest transactions are loaded, and traces do not pass through it");
  row("Error", n.error);
  for (const b of n.balances || []) row(`${b.asset} · ${b.chain}`, num(b.amount));
  panel(
    h("h2", {}, title(n), n.mine ? badge(n) : null),
    h("div", { className: "mono" }, n.address),
    facts,
    h(
      "div",
      { className: "buttons" },
      n.mine ? h("button", { onclick: () => openWallet(n.address) }, "Verdict and origin") : h("button", { onclick: act(() => api("wallets", { address: n.address, name: "", public: false })) }, "This is mine"),
      h("button", { onclick: () => startSync([n.address]) }, n.synced_at ? "Re-sync" : "Expand"),
      h("a", { href: `${EXPLORER[chain]}/address/${n.address}`, target: "_blank", rel: "noreferrer noopener" }, "Explorer ↗")
    ),
    n.mine ? null : form
  );
}
const onSelect = selectNode;

// ---------- wallets & sync ----------

function renderWallets() {
  const mine = graph.nodes.filter((n) => n.mine);
  $("wallets").replaceChildren(
    ...(mine.length ? [] : [h("li", { className: "hint" }, "None yet.")]),
    ...mine.map((n) =>
      h(
        "li",
        {},
        h("button", { className: "rowbtn", onclick: () => openWallet(n.address) }, title(n), badge(n), n.error ? h("span", { className: "badge error", title: n.error }, "sync error") : null),
        h("span", { className: "buttons" }, h("button", { onclick: act(() => api("wallets", { address: n.address, name: n.name, public: !n.public })) }, n.public ? "Make private" : "Make public"), h("button", { onclick: act(() => api(`wallets/${n.address}`, null, "DELETE")) }, "Remove"))
      )
    )
  );
}

async function refresh() {
  [graph, analysis] = await Promise.all([api(`graph?spam=${$("spam").checked}`), api("analysis")]);
  byAddress = new Map(graph.nodes.map((n) => [n.address, n]));
  stale = true;
  renderPortfolio();
  renderWallets();
  await drawGraph();
}

async function poll() {
  const s = await api("sync");
  $("sync").disabled = s.running;
  $("status").className = s.error ? "error" : "";
  $("status").textContent = s.running ? `Syncing ${s.done}/${s.total} ${s.current === "labels" ? "(downloading labels)" : short(s.current)}` : s.error || "";
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
  const f = e.target.elements;
  api("wallets", { address: f.address.value, name: f.name.value, public: f.public.checked })
    .then(() => {
      e.target.reset();
      $("status").textContent = "";
      return refresh();
    })
    .catch(fail);
};
$("sync").onclick = () => startSync([]);
$("spam").onchange = () => refresh().catch(fail);
$("minconf").oninput = () => {
  $("minout").textContent = `${$("minconf").value}%`;
  stale = true;
  drawGraph().catch(fail);
};
$("mode").onclick = () => {
  mode = mode === "3d" ? "2d" : "3d";
  stale = true;
  drawGraph().catch(fail);
};
for (const b of document.querySelectorAll("nav button")) b.onclick = () => show(b.dataset.view);
document.addEventListener("keydown", (e) => e.key === "Escape" && closePanel());
$("legend").replaceChildren(
  ...["mine", "public", "cex", "dex", "bridge", "mixer", "defi", "service", "other", "external"].map((k) => h("li", {}, h("span", { className: "swatch", style: `background:${KINDS[k][0]}` }), KINDS[k][2])),
  ...Object.entries(LINKS).map(([kind, color]) => h("li", {}, h("span", { className: "swatch dash", style: `border-color:${color}` }), `${kind} (guess)`))
);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
refresh().then(poll).catch(fail);
