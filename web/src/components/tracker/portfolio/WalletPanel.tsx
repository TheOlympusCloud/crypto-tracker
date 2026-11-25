"use client";

import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/tracker/api";
import { scanHref } from "@/lib/tracker/exposure";
import { chainOf, pct, title } from "@/lib/tracker/kinds";
import type { Verdict, VerdictLink } from "@/lib/tracker/types";
import { Amount, Badge, BUTTON, fake, FakeList, held, HistoryNote, PRIMARY, PrivacyScoreLink, readFiat, ROW, TrustBadge, VerdictBox, Worth } from "../bits";
import { ChainMark, Coin } from "../Glyph";
import { useTracker } from "../TrackerProvider";
import { WalletGrade } from "./Grade";
import { OriginRows } from "./Origins";
import { caveats, judge } from "./verdict";

const H3 = "mt-6 mb-2 text-sm font-semibold text-foreground";
const selectAll = (el: HTMLInputElement | null) => el?.select();

/** One of my wallets: what it holds, whether it can be tied to the others, and where its funds came from. */
export function WalletPanel({ address }: { address: string }) {
  // A fresh panel per wallet, with its rename form closed.
  return <Wallet key={address} address={address} />;
}

function Wallet({ address }: { address: string }) {
  const { t } = useTranslation();
  const { analysis, byAddress, act, refresh, fail, showPath, startSync, nameOf } = useTracker();
  const [renaming, setRenaming] = useState(false);
  const n = byAddress.get(address);
  const v = analysis.verdicts.find((x) => x.address === address);
  if (!n || !v) return null;
  const fiat = readFiat(analysis.fiat);
  const owned = n.addresses ?? [];
  // An HD wallet none of whose addresses is known yet is addressed by its id.
  const id = n.addresses && !n.addresses.length ? (n.wallet ?? address) : address;
  const rename = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const name = String(new FormData(e.currentTarget).get("alias") ?? "");
    void api("wallets", { address: id, name, public: Boolean(n.public) })
      .then(refresh)
      .then(() => setRenaming(false))
      .catch(fail);
  };
  const { level, headline } = judge(v, nameOf, t);
  return (
    <div>
      <div className="flex items-center gap-3 pr-8">
        <Coin public={n.public} size={48} />
        <div className="min-w-0">
          <h2 className="break-words text-lg font-semibold text-foreground">{title(n)}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Badge public={n.public} />
            <Worth value={fiat?.wallets[address]} fiat={fiat} partly={v.checked === "partial"} />
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-start gap-1.5 break-all font-mono text-xs text-muted">
        <ChainMark chain={n.chain} size={18} />
        {address}
      </div>
      <HistoryNote verdict={v} />

      <ul className="mt-3 space-y-1.5">
        {held(n)
          .filter((b) => !fake(b))
          .map((b) => (
            <li key={`${b.chain}:${b.token}:${b.asset}`} className="flex flex-wrap items-center gap-2">
              <Amount amount={b.amount} unit={`${b.asset} · ${b.chain}`} />
              {b.trust && b.token ? <TrustBadge balance={b} /> : null}
            </li>
          ))}
      </ul>
      <FakeList balances={held(n).filter(fake)} />

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className={PRIMARY} onClick={() => showPath([], "", address)}>
          {t("tracker-portfolio:wallet.show")}
        </button>
        <button type="button" className={BUTTON} onClick={act(() => api("wallets", { address: id, name: n.name, public: !n.public }))}>
          {n.public ? t("tracker-portfolio:wallet.make_private") : t("tracker-portfolio:wallet.make_public")}
        </button>
        <button type="button" className={BUTTON} onClick={() => startSync([n.wallet || address])}>
          {t("tracker-portfolio:wallet.resync")}
        </button>
        {!n.addresses && <PrivacyScoreLink address={address} chain={chainOf(n)} />}
        <button type="button" className={BUTTON} aria-expanded={renaming} onClick={() => setRenaming(!renaming)}>
          {t("tracker-portfolio:wallet.rename")}
        </button>
      </div>
      {renaming && (
        <form onSubmit={rename} className="mt-3 flex flex-wrap items-end gap-2">
          <label className="flex min-w-0 flex-[1_1_11rem] flex-col gap-1 text-sm text-muted">
            {t("tracker-portfolio:wallet.name")}
            <input
              ref={selectAll}
              name="alias"
              defaultValue={n.name || ""}
              maxLength={80}
              autoComplete="off"
              enterKeyHint="done"
              className="min-h-9 rounded-lg border border-card-border bg-surface-inset px-3 text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin"
            />
          </label>
          <button type="submit" className={PRIMARY}>
            {t("tracker-portfolio:wallet.save_name")}
          </button>
        </form>
      )}

      {owned.length > 0 && (
        <>
          <h3 className={H3}>{t("tracker-portfolio:addresses", { count: owned.length })}</h3>
          <ul className="divide-y divide-card-border">
            {owned.map((a) => (
              <li key={a.address} className="py-2">
                <span className="flex items-start justify-between gap-2">
                  <span className="min-w-0 break-all font-mono text-xs text-foreground">{a.address}</span>
                  {/* Its deep scan runs in the browser and is not part of the wallet's grade. */}
                  <DeepScanLink address={a.address} chain={chainOf(n)} />
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
                  {(a.balances ?? []).map((b) =>
                    fake(b) ? (
                      <span key={`${b.chain}:${b.token}:${b.asset}`} className="flex flex-wrap items-center gap-2">
                        <Amount amount={b.amount} unit={b.asset} />
                        <TrustBadge balance={b} />
                      </span>
                    ) : (
                      <Amount key={`${b.chain}:${b.token}:${b.asset}`} amount={b.amount} unit={b.asset} />
                    ),
                  )}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3 className={H3}>{t("tracker-exposure:wallet.title")}</h3>
      <WalletGrade address={address} chain={chainOf(n)} />

      <h3 className={H3}>{t("tracker-portfolio:wallet.tied")}</h3>
      <VerdictBox level={level} headline={headline} />
      <ul className="mt-2">
        {v.links.map((k, i) => (
          <Evidence key={i} address={address} v={v} k={k} />
        ))}
      </ul>

      <h3 className={H3}>{v.links.length ? t("tracker-portfolio:wallet.missed") : t("tracker-portfolio:wallet.not_proof")}</h3>
      <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted">
        {caveats(v, nameOf, analysis.max_hops, t).map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>

      <h3 className={H3}>{t("tracker-portfolio:wallet.origins")}</h3>
      <p className="text-sm text-muted">{t("tracker-portfolio:wallet.origins_hint")}</p>
      <OriginRows address={address} />
    </div>
  );
}

/** One line of evidence: the wallet it leads to, how, and how sure. */
function Evidence({ address, v, k }: { address: string; v: Verdict; k: VerdictLink }) {
  const { t } = useTranslation();
  const { byAddress, nameOf, showPath } = useTracker();
  const other = byAddress.get(k.wallet) ?? { public: k.public };
  const how = [t("tracker-portfolio:hops", { count: k.path.length - 1 }), [...new Set(k.kinds)].join(" + ")];
  if (k.via.length) how.push(t("tracker-portfolio:evidence.via", { names: k.via.map(nameOf).join(", ") }));
  // A private wallet that reaches a public one is exposed; any other link only ties wallets.
  const bad = !v.public && k.public;
  return (
    <li>
      <button type="button" className={ROW} onClick={() => showPath(k.path, `${nameOf(address)} ↔ ${nameOf(k.wallet)}`, address)}>
        <Coin public={other.public} size={28} />
        <span className="min-w-0 flex-1 text-sm text-foreground">
          {k.public ? t("tracker-portfolio:evidence.public", { name: nameOf(k.wallet) }) : t("tracker-portfolio:evidence.private", { name: nameOf(k.wallet) })}
          <small className="block text-xs text-muted">{how.join(", ")}</small>
        </span>
        <span className={`num shrink-0 text-sm font-semibold ${bad ? "text-danger" : "text-warning"}`} title={t("tracker-portfolio:evidence.confidence", { pct: pct(k.confidence) })}>
          {pct(k.confidence)}
          <span className="sr-only"> {t("tracker-portfolio:evidence.confidence_sr")}</span>
        </span>
      </button>
    </li>
  );
}

/** am-i-exposed's scan of one address of an HD wallet. */
function DeepScanLink({ address, chain }: { address: string; chain: string }) {
  const { t } = useTranslation();
  const href = scanHref(address, chain);
  return href ? (
    <a href={href} className="shrink-0 text-xs text-bitcoin hover:underline underline-offset-4">
      {t("tracker-exposure:wallet.deep_scan")}
    </a>
  ) : null;
}
