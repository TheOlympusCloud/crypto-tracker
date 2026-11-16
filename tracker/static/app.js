import { create2d } from "./graph2d.js";
import { HATCHED, KINDS, LINKS, PATH, STONE, VERDICTS, glyphEl, kindOf, linkDash, pct, short, sourceKind, title } from "./kinds.js";

const EXPLORER = { bitcoin: "https://mempool.space", ethereum: "https://eth.blockscout.com" };
const $ = (id) => document.getElementById(id);
// All text reaches the DOM as text nodes: labels and token names come from strangers.
function h(tag, props, ...children) {
  const { data, ...rest } = props || {};
  const el = Object.assign(document.createElement(tag), rest);
  Object.assign(el.dataset, data);
  el.append(...children.flat().filter((c) => c != null && c !== false));
  return el;
}
const num = (x) => Number(x).toLocaleString("en", { maximumSignificantDigits: 5 });
const day = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "?");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const addresses = (n) => `${n} address${n === 1 ? "" : "es"}`;
const badge = (n) => h("span", { className: `tag ${n.public ? "public" : "private"}` }, n.public ? "public" : "private");
const icon = (kind, size) => glyphEl(KINDS[kind][1], KINDS[kind][0], size);
// One of my wallets is a coin: sealed when private, open when public.
const coin = (n, size = 40) => h("span", { className: "coin" }, icon(n.public ? "public" : "mine", size));
const amount = (b, unit = b.asset) => h("span", { className: "amt" }, h("b", { className: "mono" }, num(b.amount)), h("small", {}, unit));
function verdictBox(tag, verdict, ...more) {
  const [color, shape] = VERDICTS[verdict.level];
  return h(tag, { className: `verdict ${verdict.level}` }, glyphEl(shape, color, 20), h("span", {}, h("b", {}, verdict.headline), ...more));
}
// A bar segment: solid, or hatched for the kinds listed in HATCHED, so it reads without colour.
const seg = (kind, share, tip) => h("i", { className: HATCHED.has(kind) ? "hatch" : "", style: `width:${share * 100}%;--c:${KINDS[kind][0]}`, title: tip || "" });
// Entrance animations run once per visit to a view, not on every refresh of its data.
const still = matchMedia("(prefers-reduced-motion: reduce)");
function reveal(section) {
  if (still.matches) return;
  section.classList.add("fresh");
  clearTimeout(section.timer);
  section.timer = setTimeout(() => section.classList.remove("fresh"), 1600);
}

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

// ---------- data that is on its way ----------
// Fiat values, HD wallets and alerts are not in the API yet. Each has a slot in the page that
// is filled through one of these readers, and stays hidden while its reader returns nothing.

// { currency: "EUR", total, private, public, assets: { symbol: value }, wallets: { address: value } }
const readFiat = () => (analysis.fiat && analysis.fiat.currency ? analysis.fiat : null);
// An HD wallet is one node that owns many addresses: [{ address, balances: [...] }]
const readAddresses = (n) => n.addresses || [];
// { unread: 2, items: [{ time: 1794789576, text: "…", address: "…" }] }
const readAlerts = () => analysis.alerts || { unread: 0, items: [] };

function money(value, currency) {
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency, maximumFractionDigits: Math.abs(value) < 100 ? 2 : 0 }).format(value);
  } catch {
    return `${num(value)} ${currency}`;
  }
}
const worth = (value, fiat) => (fiat && value != null ? h("span", { className: "worth mono" }, money(value, fiat.currency)) : null);

let graph = { nodes: [], edges: [], links: [] };
let analysis = { verdicts: [], origins: {}, max_hops: 4 };
let byAddress = new Map();
let shownPath = [];
const nameOf = (a) => (byAddress.has(a) ? title(byAddress.get(a)) : short(a));

// ---------- graph view: 3D where it works, flat otherwise ----------

const renderers = {};
let mode = matchMedia("(max-width: 760px)").matches ? "2d" : "3d";
let stale = true; // the active renderer has not seen the current data
// Labels are drawn on canvases, which do not wait for web fonts by themselves.
const fonts = Promise.all(["600 16px Cinzel", "500 16px Jost", "400 16px 'JetBrains Mono'"].map((f) => document.fonts.load(f))).catch(() => {});

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
  await fonts;
  const active = await renderer();
  for (const [name, r] of Object.entries(renderers)) r.setActive(name === mode);
  // hook: an animated 2D/3D switch can key on body[data-mode]; today the two swap at once.
  document.body.dataset.mode = mode;
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
  document.body.dataset.view = view;
  reveal($(`view-${view}`));
  return drawGraph().catch(fail);
}

// ---------- portfolio ----------

function renderPortfolio() {
  const mine = graph.nodes.filter((n) => n.mine);
  const fiat = readFiat();
  const assets = new Map(); // symbol -> totals
  for (const n of mine)
    for (const b of n.balances || []) {
      const a = assets.get(b.asset) || { asset: b.asset, private: 0, public: 0, chains: new Set() };
      a[n.public ? "public" : "private"] += b.amount;
      a.chains.add(b.chain);
      assets.set(b.asset, a);
    }
  const rows = [...assets.values()].sort((a, b) => a.asset.localeCompare(b.asset));

  $("totals").hidden = !fiat;
  if (fiat)
    $("totals").replaceChildren(
      ...[["Total", fiat.total, ""], ["Private", fiat.private, "private"], ["Public", fiat.public, "public"]].map(([term, value, key]) =>
        h("div", { className: key }, h("dt", {}, term), h("dd", { className: "mono" }, value == null ? "–" : money(value, fiat.currency)))
      )
    );

  const side = (key, text, hint) => {
    const wallets = mine.filter((n) => Boolean(n.public) === (key === "public"));
    return h(
      "div",
      { className: `vault ${key}` },
      coin({ public: key === "public" }, 56),
      h("h3", {}, text, h("span", { className: "count" }, plural(wallets.length, "wallet"))),
      h("p", { className: "hint" }, hint),
      h("ul", { className: "amounts" }, rows.filter((a) => a[key]).map((a) => h("li", {}, amount({ amount: a[key], asset: a.asset })))),
      rows.some((a) => a[key]) ? null : h("p", { className: "hint" }, "Nothing here yet.")
    );
  };
  $("summary").replaceChildren(
    side("private", "Private", "Wallets nobody should be able to tie to you."),
    side("public", "Public", "Wallets tied to your identity: KYC exchange withdrawals, addresses you shared.")
  );

  const columns = ["Asset", "Total", "Private", "Public", ...(fiat ? ["Value"] : []), "Split", "Chains"];
  $("assets").replaceChildren(
    rows.length
      ? h(
          "table",
          {},
          h("thead", {}, h("tr", {}, ...columns.map((t) => h("th", { scope: "col" }, t)))),
          h(
            "tbody",
            {},
            rows.map((a) => {
              const total = a.private + a.public;
              const share = total ? a.private / total : 0;
              return h(
                "tr",
                {},
                h("th", { scope: "row" }, a.asset),
                h("td", { className: "mono", data: { l: "Total" } }, num(total)),
                h("td", { className: "mono", data: { l: "Private" } }, num(a.private)),
                h("td", { className: "mono", data: { l: "Public" } }, num(a.public)),
                fiat ? h("td", { className: "mono", data: { l: "Value" } }, fiat.assets?.[a.asset] == null ? "–" : money(fiat.assets[a.asset], fiat.currency)) : null,
                h("td", { className: "splitcell" }, h("span", { className: "bar", role: "img", ariaLabel: `${Math.round(share * 100)}% private, ${Math.round(100 - share * 100)}% public` }, seg("mine", share), seg("public", 1 - share))),
                h("td", { className: "chains", data: { l: "Chains" } }, [...a.chains].join(", "))
              );
            })
          )
        )
      : h("p", { className: "hint" }, mine.length ? "No balances yet: sync your wallets." : "Add your wallets under “Wallets & sync” to start.")
  );

  $("walletcards").replaceChildren(
    ...analysis.verdicts.map((v, i) => {
      const n = byAddress.get(v.address);
      const origin = (analysis.origins[v.address] || [])[0];
      const owned = readAddresses(n).length;
      return h(
        "button",
        { className: "card wallet", style: `--i:${i}`, onclick: () => openWallet(v.address) },
        h("span", { className: "head" }, coin(n), h("span", {}, h("span", { className: "name" }, title(n)), h("span", { className: "mono" }, owned ? addresses(owned) : short(v.address)))),
        h("span", { className: "tags" }, badge(n), worth(fiat?.wallets?.[v.address], fiat)),
        h("span", { className: "amounts" }, (n.balances || []).slice(0, 4).map((b) => amount(b))),
        verdictBox("span", judge(v)),
        origin ? h("span", { className: "origin hint" }, `${origin.asset} came from`, originBar(origin)) : null
      );
    })
  );
}

// ---------- verdicts ----------

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
    asset.sources.map((s) => seg(sourceKind(s), s.share, `${pct(s.share)} ${s.name || short(s.address)}`))
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
        { className: "rows" },
        a.sources.map((s) =>
          h(
            "li",
            {},
            h(
              "button",
              { className: "rowbtn", onclick: () => showPath(s.path, `Origin of ${a.asset} in ${nameOf(address)}`, address) },
              icon(sourceKind(s), 14),
              h("b", { className: "mono" }, pct(s.share)),
              h("span", {}, `${s.name || short(s.address)} · ${KINDS[sourceKind(s)][2]}`, h("small", {}, `${num(s.amount)} ${a.asset} · ${plural(s.path.length - 1, "hop")}${s.reason ? ` · followed no further: ${s.reason}` : ""}`))
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

let folded = false; // the legend was closed to make room for the panel
function panel(...children) {
  $("detail").replaceChildren(h("button", { className: "close", ariaLabel: "Close details", onclick: closePanel }, "✕"), ...children.filter(Boolean));
  $("detail").hidden = false;
  $("detail").scrollTop = 0;
  document.body.classList.add("with-detail"); // the graph makes room instead of hiding under it
  if ($("legendbox").open && $("canvas").clientWidth < 900) {
    $("legendbox").open = false; // the panel took the legend's room: it comes back with it
    folded = true;
  }
}
function closePanel() {
  $("detail").hidden = true;
  if (folded) $("legendbox").open = true;
  folded = false;
  document.body.classList.remove("with-detail");
}
const heading = (mark, name, ...more) => h("div", { className: "head" }, mark, h("div", {}, h("h2", {}, name), ...more));

// One line of evidence: the wallet it leads to, how, and how sure.
function evidence(address, v, k) {
  const other = byAddress.get(k.wallet) || { public: k.public };
  const hops = k.path.length - 1;
  const kinds = [...new Set(k.kinds)].join(" + ");
  const via = k.via.length ? `, through your wallet ${k.via.map(nameOf).join(", ")}` : "";
  // A private wallet that reaches a public one is exposed; any other link only ties wallets.
  const level = !v.public && k.public ? "bad" : "warn";
  return h(
    "li",
    {},
    h(
      "button",
      { className: "rowbtn", onclick: () => showPath(k.path, `${nameOf(address)} ↔ ${nameOf(k.wallet)}`, address) },
      coin(other, 28),
      h("span", {}, `${nameOf(k.wallet)} (${k.public ? "public" : "private"})`, h("small", {}, `${plural(hops, "hop")}, ${kinds}${via}`)),
      h("span", { className: `conf ${level}`, title: `${pct(k.confidence)} confidence` }, pct(k.confidence), h("span", { className: "sr" }, " confidence"))
    )
  );
}

function openWallet(address) {
  const n = byAddress.get(address);
  const v = analysis.verdicts.find((x) => x.address === address);
  if (!n || !v) return;
  const fiat = readFiat();
  const owned = readAddresses(n);
  const wallet = (pub) => act(() => api("wallets", { address, name: n.name, public: pub }));
  panel(
    heading(coin(n, 48), title(n), badge(n), worth(fiat?.wallets?.[address], fiat)),
    h("div", { className: "mono" }, address),
    h("ul", { className: "amounts" }, (n.balances || []).map((b) => h("li", {}, amount(b, `${b.asset} · ${b.chain}`)))),
    h(
      "div",
      { className: "buttons" },
      h("button", { className: "primary", onclick: () => showPath([], "", address) }, "Show in graph"),
      h("button", { onclick: wallet(!n.public) }, n.public ? "Make private" : "Make public"),
      h("button", { onclick: () => startSync([address]) }, "Re-sync")
    ),
    owned.length ? h("h3", {}, addresses(owned.length)) : null,
    owned.length ? h("ul", { className: "rows addresses" }, owned.map((a) => h("li", {}, h("span", { className: "mono" }, a.address), h("span", { className: "amounts" }, (a.balances || []).map((b) => amount(b)))))) : null,
    h("h3", {}, "Can it be tied to your other wallets?"),
    verdictBox("p", judge(v)),
    h("ul", { className: "rows" }, v.links.map((k) => evidence(address, v, k))),
    h("h3", {}, v.links.length ? "What this check may still have missed" : "This is not proof of privacy"),
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
  const mark = (a) => {
    const n = byAddress.get(a) || {};
    return n.mine ? coin(n, 20) : icon(kindOf(n), 14);
  };
  panel(
    h("h2", {}, heading),
    h("p", { className: "hint" }, `${plural(path.length - 1, "hop")}, drawn as the thick red line with numbered hops.`),
    h(
      "ol",
      { className: "path" },
      path.map((a, i) =>
        h(
          "li",
          {},
          h("button", { className: "rowbtn", onclick: () => selectNode(byAddress.get(a)) }, mark(a), h("span", {}, nameOf(a), h("small", {}, KINDS[kindOf(byAddress.get(a) || {})][2]))),
          i < path.length - 1 ? h("div", { className: "hop" }, h("span", { className: "conf bad" }, String(i + 1)), h("ul", { className: "hint" }, hopText(a, path[i + 1]).map((t) => h("li", {}, t)))) : null
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
    heading(n.mine ? coin(n, 48) : h("span", { className: "coin" }, icon(kindOf(n), 32)), title(n), n.mine ? badge(n) : null),
    h("div", { className: "mono" }, n.address),
    facts,
    h(
      "div",
      { className: "buttons" },
      n.mine ? h("button", { className: "primary", onclick: () => openWallet(n.address) }, "Verdict and origin") : h("button", { onclick: act(() => api("wallets", { address: n.address, name: "", public: false })) }, "This is mine"),
      h("button", { onclick: () => startSync([n.address]) }, n.synced_at ? "Re-sync" : "Expand"),
      h("a", { className: "button", href: `${EXPLORER[chain]}/address/${n.address}`, target: "_blank", rel: "noreferrer noopener" }, "Explorer ↗")
    ),
    n.mine ? null : form
  );
}
const onSelect = selectNode;

// ---------- alerts ----------

function renderAlerts() {
  const { unread, items } = readAlerts();
  $("alerts").hidden = !items.length;
  if (!items.length) $("alertlist").hidden = true;
  $("alertcount").hidden = !unread;
  $("alertcount").textContent = unread > 99 ? "99+" : String(unread || "");
  $("alerts").ariaLabel = `Alerts, ${unread || 0} unread`;
  $("alertlist").replaceChildren(
    h("h2", { className: "eyebrow" }, "Alerts"),
    h(
      "ul",
      { className: "rows" },
      items.map((a) =>
        h("li", {}, h("button", { className: "rowbtn", disabled: !byAddress.has(a.address), onclick: () => openWallet(a.address) }, glyphEl(VERDICTS.bad[1], VERDICTS.bad[0], 16), h("span", {}, a.text, h("small", { className: "mono" }, day(a.time)))))
      )
    )
  );
}
$("alerts").onclick = () => {
  $("alertlist").hidden = !$("alertlist").hidden;
  $("alerts").ariaExpanded = String(!$("alertlist").hidden);
};

// ---------- wallets & sync ----------

function renderWallets() {
  const mine = graph.nodes.filter((n) => n.mine);
  $("wallets").replaceChildren(
    ...(mine.length ? [] : [h("li", { className: "hint" }, "None yet.")]),
    ...mine.map((n) =>
      h(
        "li",
        {},
        h("button", { className: "rowbtn", onclick: () => openWallet(n.address) }, coin(n, 28), h("span", {}, title(n), h("small", { className: "mono" }, short(n.address))), badge(n), n.error ? h("span", { className: "tag error", title: n.error }, "sync error") : null),
        h("span", { className: "buttons" }, h("button", { onclick: act(() => api("wallets", { address: n.address, name: n.name, public: !n.public })) }, n.public ? "Make private" : "Make public"), h("button", { onclick: act(() => api(`wallets/${n.address}`, null, "DELETE")) }, "Remove"))
      )
    )
  );
}

let first = true;
async function refresh() {
  [graph, analysis] = await Promise.all([api(`graph?spam=${$("spam").checked}`), api("analysis")]);
  byAddress = new Map(graph.nodes.map((n) => [n.address, n]));
  stale = true;
  if (first) reveal($("view-portfolio"));
  first = false;
  renderPortfolio();
  renderWallets();
  renderAlerts();
  await drawGraph();
}

async function poll() {
  const s = await api("sync");
  $("sync").disabled = s.running;
  $("status").className = s.error ? "error" : s.running ? "busy" : "";
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

// ---------- legend ----------

// A sample of a graph line, drawn as in the graphs.
function lineEl(color, dash, width = 1.5) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 30 8", width: 30, height: 8, class: "glyph", "aria-hidden": "true" })) svg.setAttribute(k, v);
  const path = svg.appendChild(document.createElementNS(NS, "path"));
  for (const [k, v] of Object.entries({ d: "M0 4h30", stroke: color, "stroke-width": width, "stroke-dasharray": dash.join(" ") })) path.setAttribute(k, v);
  return svg;
}
$("legend-wallets").replaceChildren(h("li", {}, icon("mine", 22), "Private: sealed coin, padlock"), h("li", {}, icon("public", 22), "Public: open coin, eye"));
$("legend-lines").replaceChildren(
  h("li", {}, lineEl(PATH, [], 3.5), "Selected path"),
  h("li", {}, lineEl(STONE.quiet, [], 1), "Transfers"),
  ...Object.keys(LINKS).map((kind) => h("li", {}, lineEl(LINKS[kind], linkDash(kind)), `Guess: ${kind}`))
);
$("legend").replaceChildren(...["cex", "dex", "bridge", "mixer", "defi", "service", "other", "external"].map((k) => h("li", {}, icon(k, 14), KINDS[k][2])));
$("legendbox").open = !matchMedia("(max-width: 760px)").matches;

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
refresh().then(poll).catch(fail);
