"use client";

import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";
import { api } from "@/lib/tracker/api";
import { chainOf, kindOf, pct, title, typeOf } from "@/lib/tracker/kinds";
import type { LookupResult, Resolved } from "@/lib/tracker/types";
import { useTracker, type PanelState } from "@/components/tracker/TrackerProvider";
import { ChainMark, Coin, KindIcon } from "@/components/tracker/Glyph";
import { Amount, Badge, BUTTON, fake, FakeList, PRIMARY, PrivacyScoreLink, ROW, Tag, TrustBadge, VerdictBox, Worth } from "@/components/tracker/bits";
import { caveats, explorerHref, moved, type Party } from "./logic";
import { useLookup } from "./useLookup";

type LookupPanelState = Exclude<PanelState, { kind: "wallet" | "node" | "path" | "group" }>;

const DRAWN = 60; // counterparties drawn in the graph (lookup.py DRAWN)
const H2 = "break-words text-xl font-semibold text-foreground";
const H3 = "eyebrow mt-6 mb-2";
const HINT = "text-sm text-muted";
const ADDRESS = "mt-2 flex items-center gap-2 break-all font-mono text-sm text-foreground";

/** The panel of a lookup: the chains question, the wait, the error, or the looked-up address itself. */
export function LookupPanel({ panel }: { panel: LookupPanelState }) {
  switch (panel.kind) {
    case "chains":
      return <Chains key={`${panel.address}:${panel.picked.join()}`} panel={panel} />;
    case "lookup-busy":
      return <Busy address={panel.address} />;
    case "lookup-error":
      return <Failed panel={panel} />;
    default:
      return <Lookup />;
  }
}

const useChainName = () => {
  const { analysis } = useTracker();
  return (c: string) => analysis.chains.find((x) => x.chain === c)?.name || c;
};

function Chains({ panel }: { panel: Extract<PanelState, { kind: "chains" }> }) {
  const { t } = useTranslation();
  const { analysis } = useTracker();
  const { openLookup } = useLookup();
  const chainName = useChainName();
  // `missing` is the EVM chain a payment code named that this server cannot read yet.
  const why = analysis.chains.find((c) => c.chain === panel.missing)?.note ?? "";
  return (
    <>
      <h2 className={H2}>{t("tracker-lookup:chains.title")}</h2>
      <div className={ADDRESS}>{panel.address}</div>
      {panel.missing && <p className="mt-3 text-sm text-danger">{t("tracker-lookup:chains.missing", { chain: chainName(panel.missing), why })}</p>}
      <p className={`mt-3 ${HINT}`}>{t("tracker-lookup:chains.hint")}</p>
      <form
        className="mt-3"
        onSubmit={(e) => {
          e.preventDefault();
          void openLookup(panel.address, new FormData(e.currentTarget).getAll("chain").map(String));
        }}
      >
        <fieldset>
          <legend className="sr-only">{t("tracker-lookup:chains.title")}</legend>
          {panel.chains.map((c) => (
            <label key={c} className="flex min-h-10 cursor-pointer items-center gap-2.5 rounded-lg px-2 text-sm text-foreground hover:bg-surface-inset">
              <input type="checkbox" name="chain" value={c} defaultChecked={panel.picked.includes(c)} className="size-4 accent-bitcoin" />
              <ChainMark chain={c} size={18} />
              {chainName(c)}
            </label>
          ))}
        </fieldset>
        <button type="submit" className={`mt-3 ${PRIMARY}`}>
          {t("tracker-lookup:chains.submit")}
        </button>
      </form>
    </>
  );
}

function Busy({ address }: { address: string }) {
  const { t } = useTranslation();
  return (
    <>
      <h2 className={H2}>{t("tracker-lookup:busy.title")}</h2>
      <div className={ADDRESS}>{address}</div>
      <p role="status" className="mt-3 animate-pulse text-sm text-bitcoin motion-reduce:animate-none">
        {t("tracker-lookup:busy.hint")}
      </p>
    </>
  );
}

function Failed({ panel }: { panel: Extract<PanelState, { kind: "lookup-error" }> }) {
  const { t } = useTranslation();
  const { openLookup } = useLookup();
  return (
    <>
      <h2 className={H2}>{t("tracker-lookup:error.title")}</h2>
      <div className={ADDRESS}>{panel.address}</div>
      <p className="mt-3 text-sm text-danger">{panel.message}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className={BUTTON} onClick={() => void openLookup(panel.address, panel.chains)}>
          {t("tracker-lookup:error.retry")}
        </button>
      </div>
    </>
  );
}

function Lookup() {
  const { t } = useTranslation();
  const { scope: v, setScope, analysis, refresh, fail, openWallet, selectNode, startSync, openPanel } = useTracker();
  const chainName = useChainName();
  // A change of state lands on the lookup it was made on, unless that one has been left since.
  const latest = useRef(v);
  useEffect(() => {
    latest.current = v;
  });
  if (!v) return null;

  // After a change of state the panel is redrawn from what is known, without reading the chain again.
  const set = (change: Partial<LookupResult>, call: () => Promise<unknown>) => () => {
    call()
      .then(() => {
        if (latest.current === v) setScope({ ...v, ...change });
        return refresh();
      })
      .catch(fail);
  };
  const track = set({ mine: 1, public: 0, watched: false }, () =>
    api("wallets", { address: v.address, name: "", public: false }).then(() => startSync([v.address])),
  );
  const watch = v.watched
    ? set({ watched: false }, () => api(`watch/${encodeURIComponent(v.address)}`, undefined, "DELETE"))
    : set({ watched: true }, () => api("watch", { address: v.address }));
  const chains = () => {
    api<Resolved>("resolve", { q: v.address })
      .then((f) => openPanel({ kind: "chains", address: v.address, chains: f.chains, picked: v.chains, missing: "" }))
      .catch(fail);
  };

  const fiat = v.value == null ? null : { currency: v.currency };
  const own = analysis.verdicts.find((x) => x.address === v.address || (v.wallet && x.wallet === v.wallet));
  const parties = v.connections;
  const yours = parties.filter((c) => c.mine);
  const others = parties.filter((c) => !c.mine);
  const kept = v.assets.filter((b) => !fake(b));
  const scams = v.assets.filter(fake);
  const r = v.risk;

  return (
    <>
      <div className="flex items-start gap-3 pr-8">
        {v.mine ? (
          <Coin public={v.public} size={48} />
        ) : (
          <span className="inline-flex size-12 shrink-0 items-center justify-center rounded-full bg-surface-inset">
            <KindIcon kind={kindOf(v)} size={32} />
          </span>
        )}
        <div className="min-w-0">
          <h2 className={H2}>{title(v)}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {v.mine ? <Badge public={v.public} /> : <Tag>{v.watched ? t("tracker-lookup:state.watched") : t("tracker-lookup:state.external")}</Tag>}
            <Worth value={v.value} fiat={fiat} />
          </div>
        </div>
      </div>
      <div className={ADDRESS}>
        <ChainMark chain={v.icon} size={18} />
        {v.address}
      </div>

      {/* Before a transfer, at a glance: is it risky to send to, and why. */}
      <div className="mt-4">
        <VerdictBox level={r.level} headline={r.headline}>
          <span className="block">{r.reason}</span>
        </VerdictBox>
        <ul className="mt-2.5 list-disc space-y-1 pl-5 text-sm text-foreground empty:hidden">
          {r.reasons
            .filter((x) => x.text !== r.reason)
            .map((x, i) => (
              <li key={i} className={x.level === "bad" ? "font-medium text-danger" : x.level === "warn" ? "text-warning" : x.level === "unknown" ? "text-muted" : ""}>
                {x.text}
              </li>
            ))}
        </ul>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {v.mine ? (
          own && (
            <button type="button" className={PRIMARY} onClick={() => openWallet(own.address)}>
              {t("tracker-lookup:action.verdict")}
            </button>
          )
        ) : (
          <>
            <button type="button" className={PRIMARY} onClick={track}>
              {t("tracker-lookup:action.track")}
            </button>
            <button type="button" className={BUTTON} onClick={watch}>
              {v.watched ? t("tracker-lookup:action.unwatch") : t("tracker-lookup:action.watch")}
            </button>
            <button type="button" className={BUTTON} onClick={() => selectNode(v.address)}>
              {v.label ? t("tracker-lookup:action.edit_label") : t("tracker-lookup:action.label")}
            </button>
          </>
        )}
        {v.chain === "evm" && (
          <button type="button" className={BUTTON} onClick={chains}>
            {t("tracker-lookup:action.chains")}
          </button>
        )}
        <PrivacyScoreLink address={v.address} chain={chainOf(v)} />
        {v.explorer_address && (
          <a href={explorerHref(v.explorer_address, v.address)} target="_blank" rel="noreferrer noopener" className={BUTTON}>
            {t("tracker-lookup:action.explorer")}
            <ExternalLink size={14} aria-hidden="true" />
          </a>
        )}
      </div>
      {!v.mine && <p className={`mt-2 ${HINT}`}>{t("tracker-lookup:action.hint")}</p>}

      <h3 className={H3}>{t("tracker-lookup:holds.title")}</h3>
      {kept.length > 0 && (
        <ul className="divide-y divide-hairline">
          {kept.map((b) => (
            <li key={`${b.chain}:${b.token}`} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2">
              <Amount amount={b.amount} unit={`${b.asset} · ${chainName(b.chain)}`} />
              {b.value == null ? <span className="text-sm text-muted">{t("tracker-lookup:holds.no_price")}</span> : <Worth value={b.value} fiat={fiat} />}
              <TrustBadge balance={b} />
            </li>
          ))}
        </ul>
      )}
      {!kept.length && !scams.length && <p className={HINT}>{t("tracker-lookup:holds.none")}</p>}
      <FakeList balances={scams} />

      <h3 className={H3}>{v.mine ? t("tracker-lookup:yours.title_mine") : t("tracker-lookup:yours.title")}</h3>
      {yours.length ? (
        <Rows parties={yours} />
      ) : (
        <VerdictBox level={v.truncated || v.errors.length ? "unknown" : "ok"} headline={t("tracker-lookup:yours.none")} />
      )}

      <h3 className={H3}>{t("tracker-lookup:parties.title", { n: others.length + v.more })}</h3>
      {others.length ? <Rows parties={others} /> : <p className={HINT}>{t("tracker-lookup:parties.none")}</p>}
      {others.length > DRAWN && <p className={`mt-2 ${HINT}`}>{t("tracker-lookup:parties.drawn", { n: DRAWN })}</p>}

      <h3 className={H3}>{t("tracker-lookup:leaves.title")}</h3>
      <ul className="list-disc space-y-1.5 rounded-lg bg-surface-inset py-3 pl-8 pr-4 text-sm text-foreground">
        {caveats(v, t).map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>
    </>
  );
}

function Rows({ parties }: { parties: Party[] }) {
  return (
    <ul className="divide-y divide-hairline">
      {parties.map((c) => (
        <li key={c.address}>
          <PartyRow c={c} />
        </li>
      ))}
    </ul>
  );
}

function PartyRow({ c }: { c: Party }) {
  const { t, i18n } = useTranslation();
  const { selectNode } = useTracker();
  const { guess, ...node } = c; // the guess is shown as its own pill, not as a guessed label
  const name = c.mine
    ? `${title(c)} (${c.public ? t("tracker:badge.public") : t("tracker:badge.private")})`
    : `${title(c)} · ${typeOf(node, t)}`;
  return (
    <button type="button" className={ROW} onClick={() => selectNode(c.address)}>
      {c.mine ? <Coin public={c.public} size={28} /> : <KindIcon kind={kindOf(c)} size={14} />}
      <span className="min-w-0 flex-1 text-sm text-foreground">
        <span className="flex items-center gap-1.5 break-words">
          <ChainMark chain={c.chain} size={14} />
          {name}
        </span>
        <small className="block text-xs text-muted">{moved(c, t, i18n.language)}</small>
      </span>
      {guess && (
        <span
          title={t("tracker-lookup:party.guess", { kind: guess.kind, pct: pct(guess.confidence) })}
          className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-xs ${
            c.mine ? "border-danger/60 bg-danger/10 text-danger" : "border-dashed border-warning/60 bg-warning/10 text-warning"
          }`}
        >
          {pct(guess.confidence)}
          <span className="sr-only"> {t("tracker-lookup:party.guess_sr", { kind: guess.kind })}</span>
        </span>
      )}
    </button>
  );
}
