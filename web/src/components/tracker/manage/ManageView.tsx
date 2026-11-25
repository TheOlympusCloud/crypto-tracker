"use client";

import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/tracker/api";
import { kindOf, short, title } from "@/lib/tracker/kinds";
import { useLookup } from "@/components/tracker/lookup/useLookup";
import { useTracker } from "../TrackerProvider";
import { ChainMark, Coin, KindIcon } from "../Glyph";
import { BUTTON, Badge, CARD, HistoryNote, PRIMARY, ROW, Tag } from "../bits";

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";
const FIELD = `h-10 w-full rounded-lg border border-card-border bg-surface-inset px-3 text-sm text-foreground placeholder:text-faint ${FOCUS}`;
const LABEL = "block space-y-1.5 text-[13px] text-muted";
const CHECK = "flex items-start gap-2 text-sm text-foreground";
const BOX = "mt-0.5 size-4 shrink-0 accent-bitcoin";
const HINT = "text-[13px] leading-relaxed text-muted";
const HEADING = "eyebrow mb-4";
const LIST_ROW = "flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-hairline py-1 last:border-0";

/** Wallets & sync: add my wallets, sync them, and see the watched addresses and the chains read. */
export function ManageView() {
  const { t } = useTranslation();
  return (
    <div className="space-y-6">
      <div className="py-2">
        <p className="eyebrow">{t("tracker:view.manage")}</p>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight text-balance text-foreground sm:text-4xl">{t("tracker-manage:hero.heading")}</h2>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">{t("tracker-manage:hero.lede")}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <AddWallet />
        <Sync />
        <Wallets />
        <WatchedWallets />
        <Chains />
      </div>
    </div>
  );
}

function AddWallet() {
  const { t } = useTranslation();
  const { refresh, fail, setStatus } = useTracker();
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    api("wallets", { address: f.get("address"), name: f.get("name"), public: f.get("public") === "on" })
      .then(() => {
        form.reset();
        setStatus({ text: "", level: "" });
        return refresh();
      })
      .catch(fail);
  };
  return (
    <form onSubmit={submit} className={`${CARD} space-y-4`}>
      <h2 className={HEADING}>{t("tracker-manage:add.heading")}</h2>
      <label className={LABEL}>
        <span className="block">{t("tracker-manage:add.address")}</span>
        <input name="address" required placeholder={t("tracker-manage:add.address_placeholder")} autoComplete="off" spellCheck={false} className={`${FIELD} font-mono`} />
      </label>
      <p className={HINT}>{t("tracker-manage:add.hint")}</p>
      <label className={LABEL}>
        <span className="block">{t("tracker-manage:add.name")}</span>
        <input name="name" placeholder={t("tracker-manage:add.name_placeholder")} maxLength={80} className={FIELD} />
      </label>
      <label className={CHECK}>
        <input type="checkbox" name="public" className={BOX} />
        {t("tracker-manage:add.public")}
      </label>
      <button type="submit" className={PRIMARY}>
        {t("tracker-manage:add.submit")}
      </button>
    </form>
  );
}

function Sync() {
  const { t } = useTranslation();
  const { sync, startSync } = useTracker();
  const [depth, setDepth] = useState(0);
  const [labels, setLabels] = useState(false);
  return (
    <div className={`${CARD} space-y-4`}>
      <h2 className={HEADING}>{t("tracker-manage:sync.heading")}</h2>
      <label className={LABEL}>
        <span className="block">{t("tracker-manage:sync.depth")}</span>
        <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} className={`${FIELD} cursor-pointer`}>
          <option value={0}>{t("tracker-manage:sync.level.0")}</option>
          <option value={1}>{t("tracker-manage:sync.level.1")}</option>
          <option value={2}>{t("tracker-manage:sync.level.2")}</option>
        </select>
      </label>
      <p className={HINT}>{t("tracker-manage:sync.hint")}</p>
      <label className={CHECK}>
        <input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} className={BOX} />
        {t("tracker-manage:sync.labels")}
      </label>
      {/* Not disabled for the background fill-in: a sync goes first. */}
      <button type="button" className={PRIMARY} disabled={Boolean(sync?.running)} onClick={() => startSync([], depth, labels)}>
        {t("tracker-manage:sync.start")}
      </button>
    </div>
  );
}

function Wallets() {
  const { t } = useTranslation();
  const { analysis, byAddress, openWallet, act } = useTracker();
  return (
    <section className={`${CARD} md:col-span-2`}>
      <h2 className={HEADING}>{t("tracker-manage:wallets.heading")}</h2>
      {/* One row per wallet: an HD wallet's addresses are listed in its panel, not here. */}
      <ul>
        {!analysis.verdicts.length && <li className={HINT}>{t("tracker-manage:wallets.none")}</li>}
        {analysis.verdicts.map((v) => {
          const n = byAddress.get(v.address);
          if (!n) return null; // byAddress holds every verdict's wallet, HD ones without a node too
          const error = n.error || v.coverage.error;
          // An HD wallet none of whose addresses is known yet is only reachable by its id.
          const id = n.addresses && !n.addresses.length ? n.wallet : n.address;
          return (
            <li key={v.address} className={LIST_ROW}>
              <button type="button" className={`${ROW} min-w-0 flex-1 basis-72`} onClick={() => openWallet(n.address)}>
                <Coin public={n.public} size={28} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-foreground">{title(n)}</span>
                  <small className="flex items-center gap-1.5 font-mono text-xs text-muted">
                    <ChainMark chain={n.chain} size={14} />
                    {n.addresses ? t("tracker-manage:wallets.addresses", { count: n.addresses.length }) : short(n.address)}
                  </small>
                  <HistoryNote verdict={v} />
                </span>
                <Badge public={n.public} />
                {error && (
                  <Tag tone="danger" title={error}>
                    {t("tracker-manage:wallets.sync_error")}
                  </Tag>
                )}
              </button>
              <span className="flex gap-2">
                <button type="button" className={BUTTON} onClick={act(() => api("wallets", { address: id, name: n.name, public: !n.public }))}>
                  {n.public ? t("tracker-manage:wallets.make_private") : t("tracker-manage:wallets.make_public")}
                </button>
                <button type="button" className={BUTTON} onClick={act(() => api(`wallets/${encodeURIComponent(n.wallet || n.address)}`, undefined, "DELETE"))}>
                  {t("tracker-manage:remove")}
                </button>
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function WatchedWallets() {
  const { t } = useTranslation();
  const { analysis, act, fail } = useTracker();
  const { lookUp } = useLookup();
  return (
    <section className={`${CARD} md:col-span-2`}>
      <h2 className="eyebrow mb-2">{t("tracker-manage:watched.heading")}</h2>
      <p className={`${HINT} mb-3`}>{t("tracker-manage:watched.hint")}</p>
      <ul>
        {!analysis.watched.length && <li className={HINT}>{t("tracker-manage:watched.none")}</li>}
        {analysis.watched.map((w) => (
          <li key={w.address} className={LIST_ROW}>
            <button type="button" className={`${ROW} min-w-0 flex-1 basis-72`} onClick={() => void lookUp(w.address).catch(fail)}>
              <KindIcon kind={kindOf(w)} size={14} />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-foreground">{title(w)}</span>
                <small className="flex items-center gap-1.5 break-all font-mono text-xs text-muted">
                  <ChainMark chain={w.chain} size={14} />
                  {w.address}
                </small>
              </span>
              <Tag>{t("tracker-manage:watched.tag")}</Tag>
            </button>
            <button type="button" className={BUTTON} onClick={act(() => api(`watch/${encodeURIComponent(w.address)}`, undefined, "DELETE"))}>
              {t("tracker-manage:remove")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Which chains are read, which wait for a key, and how deep each one goes. */
function Chains() {
  const { t } = useTranslation();
  const { analysis } = useTracker();
  return (
    <section className={`${CARD} md:col-span-2`}>
      <h2 className={HEADING}>{t("tracker-manage:chains.heading")}</h2>
      <ul className="grid gap-x-6 sm:grid-cols-2">
        {analysis.chains.map((c) => (
          <li key={c.chain} className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-hairline py-2">
            <span className="text-sm">
              <b className="font-semibold text-foreground">{c.name}</b> <small className="text-xs text-muted">{c.asset}</small>
            </span>
            <Tag tone={c.status === "ok" ? "muted" : "danger"}>{c.status === "ok" ? t("tracker-manage:chains.read") : t("tracker-manage:chains.needs_key")}</Tag>
            {c.note && <small className="basis-full text-[13px] leading-relaxed text-muted">{c.note}</small>}
          </li>
        ))}
      </ul>
    </section>
  );
}
