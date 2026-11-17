import { create2d } from "./graph2d.js";
import { HATCHED, KINDS, LINKS, PAPER, PATH, VERDICTS, glyphEl, kindOf, linkDash, pct, short, sourceKind, title } from "./kinds.js";

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

// ---------- optional data ----------
// Fiat values, HD wallet addresses and alerts each have a slot in the page that is filled
// through one of these readers, and stays hidden while its reader returns nothing.

// { currency: "EUR", total, private, public, assets: { symbol: value }, wallets: { address: value } }
const readFiat = () => (analysis.fiat && analysis.fiat.currency ? analysis.fiat : null);
// An HD wallet is one node that owns many addresses: [{ address, balances: [...] }]
const readAddresses = (n) => n.addresses || [];
// What a wallet holds: summed over its addresses when it is an HD wallet.
const held = (n) => n.wallet_balances || n.balances || [];
// { unread: 2, items: [{ time: 1794789576, text: "…", address: "…" }] }
const readAlerts = () => analysis.alerts || { unread: 0, items: [] };

function money(value, currency) {
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency, maximumFractionDigits: Math.abs(value) < 100 ? 2 : 0 }).format(value);
  } catch {
    return `${num(value)} ${currency}`;
  }
}
// A wallet that was only partly read is worth at least what was found: say "at least".
const partly = (v, el) => (el && v.checked === "partial" ? h("span", { className: "worth mono", title: "Only part of this wallet could be read: it holds at least this much" }, "≥ ", el.textContent) : el);
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
const fonts = Promise.all(["800 condensed 16px 'Bricolage Grotesque'", "500 16px 'Red Hat Text'", "400 16px 'JetBrains Mono'"].map((f) => document.fonts.load(f))).catch(() => {});

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
  // The view that becomes active fades in (style.css, #canvas); the other one is hidden.
  document.body.dataset.mode = mode;
  $("mode").ariaPressed = String(mode === "3d");
  if (stale) active.setData(scope ? scope.graph : graph, { minConf: $("minconf").value / 100 });
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

  // The ticker repeats the totals of the table below. Its second half is a copy, so the loop has no seam.
  const tape = rows.length ? rows.map((r) => [r.asset, num(r.private + r.public)]) : [["Know what you hold"], ["Know what links it"]];
  const laps = Math.max(2, 2 * Math.ceil(8 / tape.length));
  $("ticker").replaceChildren(
    ...Array.from({ length: laps }, (_, lap) => tape.map(([word, figure]) => h("span", { data: lap ? { again: "" } : {} }, word, figure ? h("i", {}, figure) : null))).flat()
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
        h("span", { className: "head" }, coin(n), h("span", {}, h("span", { className: "name" }, title(n)), h("span", { className: "mono" }, owned ? addresses(owned) : short(v.address))), h("span", { className: "go", ariaHidden: "true" })),
        h("span", { className: "tags" }, badge(n), fiat && v.checked === "none" ? h("span", { className: "worth mono", title: "Not checked, so its value is unknown" }, "—") : partly(v, worth(fiat?.wallets?.[v.address], fiat))),
        h("span", { className: "amounts" }, held(n).slice(0, 4).map((b) => amount(b))),
        verdictBox("span", judge(v)),
        origin ? h("span", { className: "origin hint" }, `${origin.asset} came from`, originBar(origin)) : null
      );
    })
  );
}

// ---------- verdicts ----------

// The headline of a wallet's verdict. "ok" never means safe, only that nothing was found.
function judge(v) {
  if (v.checked === "none") return { level: "unknown", headline: v.coverage.error ? "Not checked: the last sync failed" : "Not checked yet: sync this wallet" };
  const exposing = v.links.filter((k) => k.public !== v.public);
  const best = (v.public ? v.links : exposing)[0] || v.links[0];
  // Only a complete check may say that nothing was found.
  if (!best) return v.checked === "full" ? { level: "ok", headline: "No link to your other wallets found" } : { level: "unknown", headline: "Partly checked: the last sync had errors" };
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
  out.push(...(c.notes || []));
  out.push("Swaps outside THORChain and Maya, chains that are not read, unlabelled exchange wallets and anything off-chain (same IP address, same exchange account) are invisible to this check.");
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
  if (leave()) drawGraph().catch(fail);
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
  if (leave()) drawGraph().catch(fail);
  const fiat = readFiat();
  const owned = readAddresses(n);
  const wallet = (pub) => act(() => api("wallets", { address: n.addresses && !n.addresses.length ? n.wallet : address, name: n.name, public: pub }));
  panel(
    heading(coin(n, 48), title(n), badge(n), partly(v, worth(fiat?.wallets?.[address], fiat))),
    h("div", { className: "mono" }, address),
    h("ul", { className: "amounts" }, held(n).map((b) => h("li", {}, amount(b, `${b.asset} · ${b.chain}`)))),
    h(
      "div",
      { className: "buttons" },
      h("button", { className: "primary", onclick: () => showPath([], "", address) }, "Show in graph"),
      h("button", { onclick: wallet(!n.public) }, n.public ? "Make private" : "Make public"),
      h("button", { onclick: () => startSync([n.wallet || address]) }, "Re-sync")
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
  leave();
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
    h("p", { className: "hint" }, `${plural(path.length - 1, "hop")}, drawn as the fat orange line with numbered hops.`),
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
  if (!n) return scope ? renderLookup() : closePanel(); // a tap beside the graph does not end a lookup
  const form = h(
    "form",
    {},
    h("label", {}, "Label ", h("input", { name: "label", value: n.label || "", maxLength: 80, placeholder: "e.g. Kraken deposit" })),
    h("label", {}, "Type ", h("select", { name: "category" }, ["cex", "dex", "bridge", "mixer", "defi", "service", "other"].map((c) => h("option", { value: c, selected: c === (n.category || "other") }, KINDS[c][2])))),
    h("button", {}, "Save label")
  );
  form.onsubmit = (e) => {
    e.preventDefault();
    const label = { name: form.elements.label.value, category: form.elements.category.value };
    act(() => api("labels", { address: n.address, ...label }))();
    // The looked-up wallet is not part of the data a refresh brings: rename it here.
    if (scope) for (const x of [scope, ...scope.connections, ...scope.graph.nodes]) if (x.address === n.address) Object.assign(x, { label: label.name, category: label.name ? label.category : undefined });
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
      scope ? h("button", { onclick: renderLookup }, `Back to ${title(scope)}`) : h("button", { onclick: () => startSync([n.address]) }, n.synced_at ? "Re-sync" : "Expand"),
      n.mine || n.address === scope?.address ? null : h("button", { onclick: () => lookUp(n.address).catch(fail) }, "Look up"),
      n.explorer_address ? h("a", { className: "button", href: n.explorer_address.replace("{}", encodeURIComponent(n.address.replace("bitcoincash:", ""))), target: "_blank", rel: "noreferrer noopener" }, "Explorer ↗") : null
    ),
    n.mine ? null : form
  );
}
const onSelect = selectNode;

// ---------- lookup: any address, mine or not ----------

// The looked-up wallet whose own connections are in the graph instead of mine. It is read
// live and kept only here: closing its panel brings my graph back.
let scope = null;
let asked = 0; // only the newest lookup may fill the panel
function leave() {
  if (!scope) return false;
  scope = null;
  stale = true;
  return true;
}
const chainName = (c) => (analysis.chains || []).find((x) => x.chain === c)?.name || c;
const sums = (list) => list.map((b) => `${num(b.amount)} ${b.asset}`).join(", ");

// From a pasted or scanned line to the wallet view. Only an EVM address needs a question.
async function lookUp(text) {
  $("status").textContent = "";
  const found = await api("resolve", { q: text });
  $("lookup").elements.q.value = found.address;
  const missing = found.needs_key ? `This code is for ${chainName(found.needs_key)}, which this server cannot read without an Etherscan API key (CT_ETHERSCAN_KEY). The address can still be read on these chains.` : "";
  if (found.chain === "evm" && !found.hint && (missing || found.chains.length > 1)) return askChains(found.address, found.chains, found.chains, missing);
  return openLookup(found.address, found.hint ? [found.hint] : []);
}

function askChains(address, chains, picked, missing = "") {
  const form = h(
    "form",
    {},
    chains.map((c) => h("label", { className: "row" }, h("input", { type: "checkbox", value: c, checked: picked.includes(c) }), chainName(c))),
    h("button", { className: "primary" }, "Look up")
  );
  form.onsubmit = (e) => {
    e.preventDefault();
    openLookup(address, [...form.querySelectorAll("input:checked")].map((i) => i.value));
  };
  panel(h("h2", {}, "Which chains?"), h("div", { className: "mono" }, address), missing ? h("p", { className: "error" }, missing) : null, h("p", { className: "hint" }, "The same address exists on every EVM chain. Each chain read is one more indexer that sees it."), form);
}

async function openLookup(address, chains = []) {
  const mine = ++asked;
  panel(h("h2", {}, "Looking up"), h("div", { className: "mono" }, address), h("p", { className: "hint busy", role: "status" }, "Reading it from the indexers. A busy address takes a little while."));
  try {
    const v = await api(`lookup/${encodeURIComponent(address)}${chains.length ? `?chains=${chains.join(",")}` : ""}`);
    if (mine !== asked) return;
    scope = v;
    stale = true;
    shownPath = [];
    renderLookup();
    await show("graph");
    (await renderer()).focus(address);
  } catch (e) {
    if (mine !== asked) return;
    panel(h("h2", {}, "Could not look it up"), h("div", { className: "mono" }, address), h("p", { className: "error" }, e.message), h("div", { className: "buttons" }, h("button", { onclick: () => openLookup(address, chains) }, "Try again")));
  }
}

function renderLookup() {
  const v = scope;
  const fiat = v.value == null ? null : { currency: v.currency };
  const own = analysis.verdicts.find((x) => x.address === v.address || (v.wallet && x.wallet === v.wallet));
  const state = v.mine ? badge(v) : h("span", { className: `tag ${v.watched ? "watched" : "external"}` }, v.watched ? "watched" : "not yours");
  // After a change of state the panel is redrawn from what is known, without reading the chain again.
  const set = (change, call) => () =>
    call()
      .then(() => Object.assign(v, change))
      .then(refresh)
      .then(renderLookup)
      .catch(fail);
  const track = set({ mine: 1, public: 0, watched: false }, () => api("wallets", { address: v.address, name: "", public: false }).then(() => startSync([v.address])));
  const watch = v.watched ? set({ watched: false }, () => api(`watch/${encodeURIComponent(v.address)}`, null, "DELETE")) : set({ watched: true }, () => api("watch", { address: v.address }));
  const party = (c) => {
    const kind = KINDS[kindOf(c)][2];
    const moved = [c.received.length ? `in ${sums(c.received)}` : "", c.sent.length ? `out ${sums(c.sent)}` : "", c.count ? plural(c.count, "transfer") : "", c.count ? (c.first === c.last ? day(c.first) : `${day(c.first)} to ${day(c.last)}`) : ""];
    return h(
      "li",
      {},
      h(
        "button",
        { className: "rowbtn", onclick: () => selectNode(c) },
        c.mine ? coin(c, 28) : icon(kindOf(c), 14),
        h("span", {}, c.mine ? `${title(c)} (${c.public ? "public" : "private"})` : `${title(c)} · ${kind}`, h("small", {}, moved.filter(Boolean).join(" · "))),
        c.guess ? h("span", { className: `conf ${c.mine ? "bad" : "warn"}`, title: `Guess that both have one owner: ${c.guess.kind}, ${pct(c.guess.confidence)} confidence` }, pct(c.guess.confidence), h("span", { className: "sr" }, ` ${c.guess.kind} guess`)) : null
      )
    );
  };
  const yours = v.connections.filter((c) => c.mine);
  const others = v.connections.filter((c) => !c.mine);
  const gaps = [
    ...v.errors.map((e) => `Not everything could be read (${e}).`),
    v.truncated ? "This address is very active and only its newest transactions are loaded: older counterparties and the tokens it holds are missing." : null,
    v.hidden ? `${plural(v.hidden, "token")} without a trusted price ${v.hidden === 1 ? "is" : "are"} left out, with the transfers of such tokens: anyone can airdrop those.` : null,
    v.more ? `${plural(v.more, "quieter counterparty").replace("partys", "parties")} not listed.` : null,
    ...v.notes,
    v.mine ? null : "Only direct dealings with your wallets show here. Links that run through other addresses are searched once you track it.",
    "It was read live and nothing about it is kept: it takes no part in your verdicts unless you track it. The indexer of its chain did see this lookup.",
  ];
  panel(
    heading(v.mine ? coin(v, 48) : h("span", { className: "coin" }, icon(kindOf(v), 32)), title(v), state, worth(v.value, fiat)),
    h("div", { className: "mono" }, v.address),
    v.chain === "evm" ? h("p", { className: "hint" }, `Read on ${v.chains.map(chainName).join(", ") || "no chain"}.`) : null,
    h(
      "div",
      { className: "buttons" },
      v.mine ? (own ? h("button", { className: "primary", onclick: () => openWallet(own.address) }, "Verdict and origin") : null) : h("button", { className: "primary", onclick: track }, "Track as mine"),
      v.mine ? null : h("button", { onclick: watch }, v.watched ? "Stop watching" : "Watch"),
      v.mine ? null : h("button", { onclick: () => selectNode(v) }, v.label ? "Edit label" : "Label"),
      v.chain === "evm" ? h("button", { onclick: () => api("resolve", { q: v.address }).then((f) => askChains(v.address, f.chains, v.chains)).catch(fail) }, "Chains") : null,
      v.explorer_address ? h("a", { className: "button", href: v.explorer_address.replace("{}", encodeURIComponent(v.address.replace("bitcoincash:", ""))), target: "_blank", rel: "noreferrer noopener" }, "Explorer ↗") : null
    ),
    v.mine ? null : h("p", { className: "hint" }, "Tracking adds it to your wallets, as private, and syncs it. Watching only keeps it at hand."),
    h("h3", {}, "What it holds"),
    v.assets.length ? h("ul", { className: "rows addresses holdings" }, v.assets.map((b) => h("li", {}, amount(b, `${b.asset} · ${chainName(b.chain)}`), worth(b.value, fiat)))) : h("p", { className: "hint" }, "Nothing found."),
    h("h3", {}, v.mine ? "Your other wallets it dealt with" : "Links to your wallets"),
    yours.length
      ? h("ul", { className: "rows" }, yours.map(party))
      : verdictBox("p", { level: v.truncated || v.errors.length ? "unknown" : "ok", headline: "No direct dealing with your wallets found" }),
    h("h3", {}, `Counterparties (${others.length + v.more})`),
    others.length ? h("ul", { className: "rows" }, others.map(party)) : h("p", { className: "hint" }, "None found."),
    others.length > 60 ? h("p", { className: "hint" }, "The graph shows the 60 busiest.") : null,
    h("h3", {}, "What this view leaves out"),
    h("ul", { className: "caveats" }, gaps.filter(Boolean).map((c) => h("li", {}, c)))
  );
}

function renderWatched() {
  const watched = analysis.watched || [];
  $("watched").replaceChildren(
    ...(watched.length ? [] : [h("li", { className: "hint" }, "None yet. Look an address up and press Watch.")]),
    ...watched.map((w) =>
      h(
        "li",
        {},
        h("button", { className: "rowbtn", onclick: () => lookUp(w.address).catch(fail) }, icon(kindOf(w), 14), h("span", {}, title(w), h("small", { className: "mono" }, w.address)), h("span", { className: "tag watched" }, "watched")),
        h("span", { className: "buttons" }, h("button", { onclick: act(() => api(`watch/${encodeURIComponent(w.address)}`, null, "DELETE")) }, "Remove"))
      )
    )
  );
}

$("lookup").onsubmit = (e) => {
  e.preventDefault();
  const q = e.target.elements.q.value.trim();
  if (q) lookUp(q).catch(fail);
};

// ---------- QR scan ----------

// The browser's own detector where it reads QR codes (Android, macOS); otherwise the vendored one.
const native = "BarcodeDetector" in window ? BarcodeDetector.getSupportedFormats().then((f) => (f.includes("qr_code") ? new BarcodeDetector({ formats: ["qr_code"] }) : null), () => null) : Promise.resolve(null);
// What a video frame or a photo says, or "" when no QR code is found in it.
async function decode(source, width, height) {
  const detector = await native;
  if (detector) return (await detector.detect(source))[0]?.rawValue || "";
  await import("./vendor/jsQR.js"); // not a module: it defines self.jsQR
  // A phone photo is far larger than needed; a small code in it may still need the second pass.
  for (const max of [1000, 2400]) {
    const scale = Math.min(1, max / Math.max(width, height));
    const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(width * scale), height: Math.round(height * scale) });
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const found = self.jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
    if (found?.data) return found.data;
    if (scale === 1) break;
  }
  return "";
}

let stream = null;
let scanning = 0; // bumped to stop the loop that reads the camera
const scanHint = (text) => ($("scanhint").textContent = text);
function stopCamera() {
  scanning++;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  $("camera").srcObject = null;
  $("camera").hidden = true;
}
// The scanned text is not shown as it is: a QR code can hold anything, a private key too.
function scanned(text) {
  $("scanner").close();
  $("lookup").elements.q.value = "";
  lookUp(text).catch(fail);
}

async function openScanner() {
  $("scanner").showModal();
  const instead = "Take a photo of the code instead, or paste the address.";
  // Browsers only give the camera to pages served over HTTPS or from localhost.
  if (!navigator.mediaDevices?.getUserMedia) return scanHint(`${isSecureContext ? "This browser gives no camera here." : "The live camera needs HTTPS."} ${instead}`);
  scanHint("Asking for the camera…");
  const run = ++scanning;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
  } catch {
    return scanHint(`No camera, or it was not allowed. ${instead}`);
  }
  if (run !== scanning || !$("scanner").open) return stopCamera(); // closed while the browser was asking
  const video = $("camera");
  video.srcObject = stream;
  video.hidden = false;
  await video.play().catch(() => {});
  scanHint("Point the camera at the wallet's QR code.");
  while (run === scanning) {
    const text = video.videoWidth ? await decode(video, video.videoWidth, video.videoHeight).catch(() => "") : "";
    if (run !== scanning) return;
    if (text) return scanned(text);
    await new Promise((r) => setTimeout(r, 200));
  }
}
$("scan").onclick = () => openScanner().catch(fail);
$("scanclose").onclick = () => $("scanner").close();
$("scanner").onclose = stopCamera; // the Close button, Escape and a successful scan all end here
$("photo").onchange = async () => {
  const [file] = $("photo").files;
  $("photo").value = ""; // so that the same photo can be chosen again
  if (!file) return;
  scanHint("Reading the photo…");
  try {
    const image = await createImageBitmap(file);
    const text = await decode(image, image.width, image.height);
    if (text) scanned(text);
    else scanHint("No QR code found in that photo. Try again, closer and straight on.");
  } catch {
    scanHint("That file could not be read as an image.");
  }
};

// ---------- alerts ----------

function renderAlerts() {
  const { unread, items } = readAlerts();
  $("alerts").hidden = !items.length;
  if (!items.length) $("alertlist").hidden = true;
  $("alertcount").hidden = !unread;
  $("alertcount").nextElementSibling.hidden = Boolean(unread); // the plain arrow stands in for the count
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
  if (!$("alertlist").hidden && readAlerts().unread) api("alerts/seen", {}).then(refresh).catch(fail);
};

// ---------- wallets & sync ----------

function renderWallets() {
  // One row per wallet: an HD wallet's addresses are listed in its panel, not here.
  const failed = new Map(analysis.verdicts.map((v) => [v.address, v.coverage.error]));
  const mine = analysis.verdicts.map((v) => byAddress.get(v.address));
  $("wallets").replaceChildren(
    ...(mine.length ? [] : [h("li", { className: "hint" }, "None yet.")]),
    ...mine.map((n) =>
      h(
        "li",
        {},
        h("button", { className: "rowbtn", onclick: () => openWallet(n.address) }, coin(n, 28), h("span", {}, title(n), h("small", { className: "mono" }, n.addresses ? addresses(n.addresses.length) : short(n.address))), badge(n), n.error || failed.get(n.address) ? h("span", { className: "tag error", title: n.error || failed.get(n.address) }, "sync error") : null),
        h("span", { className: "buttons" }, h("button", { onclick: act(() => api("wallets", { address: n.addresses && !n.addresses.length ? n.wallet : n.address, name: n.name, public: !n.public })) }, n.public ? "Make private" : "Make public"), h("button", { onclick: act(() => api(`wallets/${encodeURIComponent(n.wallet || n.address)}`, null, "DELETE")) }, "Remove"))
      )
    )
  );
}

// Which chains are read, which wait for a key, and how deep each one goes.
function renderChains() {
  $("chains").replaceChildren(
    ...(analysis.chains || []).map((c) =>
      h("li", {}, h("span", {}, h("b", {}, c.name), h("small", {}, ` ${c.asset}`)), h("span", { className: `tag ${c.status === "ok" ? "private" : "error"}` }, c.status === "ok" ? "read" : "needs key"), c.note ? h("small", { className: "hint" }, c.note) : null)
    )
  );
}

let first = true;
async function refresh() {
  // Asking for the portfolio first refreshes prices older than ten minutes; values are optional.
  await api("portfolio").catch(() => null);
  [graph, analysis] = await Promise.all([api(`graph?spam=${$("spam").checked}`), api("analysis")]);
  byAddress = new Map(graph.nodes.map((n) => [n.address, n]));
  // An HD wallet none of whose addresses was found has a verdict but no node in the graph.
  for (const v of analysis.verdicts)
    if (!byAddress.has(v.address)) byAddress.set(v.address, { address: v.address, wallet: v.wallet, name: v.name, public: v.public, mine: 1, addresses: [] });
  stale = true;
  if (first) reveal($("view-portfolio"));
  first = false;
  renderPortfolio();
  renderWallets();
  renderWatched();
  renderChains();
  renderAlerts();
  await drawGraph();
}

async function poll() {
  const s = await api("sync");
  $("sync").disabled = s.running;
  $("status").className = s.error ? "error" : s.running ? "busy" : "";
  $("status").textContent = s.running ? `Syncing ${s.done}/${s.total} ${["labels", "swaps", "prices"].includes(s.current) ? `(${s.current})` : short(s.current)}` : s.error || (s.notes || []).join(" · ");
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
// The ticker's pause button. The choice is kept, so a ticker that was stopped stays stopped.
function pauseTicker(paused) {
  $("tickerpause").ariaPressed = String(paused);
  $("tickerpause").ariaLabel = $("tickerpause").title = paused ? "Play the ticker" : "Pause the ticker";
  $("tickerpause").parentElement.classList.toggle("paused", paused);
}
$("tickerpause").onclick = () => {
  const paused = $("tickerpause").ariaPressed !== "true";
  pauseTicker(paused);
  try {
    localStorage.setItem("ticker", paused ? "paused" : "");
  } catch {} // private browsing: the choice lasts for this visit only
};
try {
  pauseTicker(localStorage.getItem("ticker") === "paused");
} catch {}
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
  h("li", {}, lineEl(PAPER.quiet, [], 1), "Transfers"),
  ...Object.keys(LINKS).map((kind) => h("li", {}, lineEl(LINKS[kind], linkDash(kind)), `Guess: ${kind}`))
);
$("legend").replaceChildren(...["cex", "dex", "bridge", "mixer", "defi", "service", "other", "external"].map((k) => h("li", {}, icon(k, 14), KINDS[k][2])));
$("legendbox").open = !matchMedia("(max-width: 760px)").matches;

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
refresh().then(poll).catch(fail);
