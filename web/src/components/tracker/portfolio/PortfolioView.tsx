"use client";

import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, Pause, Play } from "lucide-react";
import { createLocalStorageStore } from "@/hooks/createLocalStorageStore";
import { money, num } from "@/lib/tracker/format";
import { short, title } from "@/lib/tracker/kinds";
import type { GraphNode, Verdict } from "@/lib/tracker/types";
import { Amount, Badge, Bar, CARD, fake, held, readFiat, Segment, TrustBadge, VerdictBox, Worth } from "../bits";
import { ChainMark, Coin } from "../Glyph";
import { useTracker } from "../TrackerProvider";
import { GradeChip } from "./Grade";
import { OriginBar } from "./Origins";
import { assetTotals, type AssetTotal } from "./totals";
import { judge } from "./verdict";

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";
// The ticker's pause is kept, so a ticker that was stopped stays stopped.
const pausedStore = createLocalStorageStore<boolean>("tracker-ticker", false, (raw) => raw === "paused", (paused) => (paused ? "paused" : ""));

/** Everything I hold: fiat totals, the private and public vaults, the assets and a card per wallet. */
export function PortfolioView() {
  const { t } = useTranslation();
  const { graph, analysis, loaded } = useTracker();
  const mine = graph.nodes.filter((n) => n.mine);
  const fiat = readFiat(analysis.fiat);
  const { rows, fakes } = assetTotals(mine);
  return (
    <div className="space-y-8">
      <div className="pt-2 text-center">
        <p className="eyebrow">{t("tracker-portfolio:hero.kicker")}</p>
        <h2 className="mt-3 text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-5xl">{t("tracker-portfolio:hero.heading")}</h2>
        <ul aria-label={t("tracker-portfolio:hero.stickers")} className="mt-5 flex flex-wrap justify-center gap-2">
          {[t("tracker-portfolio:hero.readonly"), t("tracker-portfolio:hero.selfhosted"), t("tracker-portfolio:hero.honest")].map((s) => (
            <li key={s} className="rounded-full border border-hairline-strong px-3 py-1 text-sm text-muted">
              {s}
            </li>
          ))}
        </ul>
        {fiat && (
          <dl className="mx-auto mt-6 grid max-w-3xl grid-cols-1 gap-3 text-left sm:grid-cols-3">
            {(
              [
                [t("tracker-portfolio:total"), fiat.total],
                [t("tracker-portfolio:private"), fiat.private],
                [t("tracker-portfolio:public"), fiat.public],
              ] as const
            ).map(([term, value], i) => (
              <div key={term} className={CARD}>
                <dt className="text-xs text-muted sm:text-sm">{term}</dt>
                <dd className={`num mt-1 text-xl font-bold sm:text-2xl ${i ? "text-foreground" : "text-bitcoin"}`}>
                  <Money value={value} currency={fiat.currency} />
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {/* Until my wallets have been read once, nothing is said about them, not even "add one". */}
      {loaded && (
        <>
          <Ticker rows={rows} />

          <div className="grid gap-3 sm:grid-cols-2">
            <Vault side="private" mine={mine} rows={rows} />
            <Vault side="public" mine={mine} rows={rows} />
          </div>

          <section>
            <SectionHead kicker={t("tracker-portfolio:assets.kicker")} heading={t("tracker-portfolio:assets.heading")} />
            <Assets rows={rows} fakes={fakes} wallets={mine.length} />
          </section>

          <section>
            <SectionHead kicker={t("tracker-portfolio:wallets.kicker")} heading={t("tracker-portfolio:wallets.heading")} />
            <p className="mb-4 max-w-[70ch] text-sm text-muted">{t("tracker-portfolio:wallets.hint")}</p>
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {analysis.verdicts.map((v) => (
                <WalletCard key={v.address} v={v} />
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

function Money({ value, currency }: { value: number | null; currency: string }) {
  const { i18n } = useTranslation();
  return <>{value == null ? "–" : money(value, currency, i18n.language)}</>;
}

function SectionHead({ kicker, heading }: { kicker: string; heading: string }) {
  return (
    <h2 className="mb-3 text-balance text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
      <span className="eyebrow mb-2 block">{kicker}</span>
      {heading}
    </h2>
  );
}

/** The ticker repeats the totals of the table below. Its second half is a copy, so the loop has no seam. */
function Ticker({ rows }: { rows: AssetTotal[] }) {
  const { t, i18n } = useTranslation();
  const paused = useSyncExternalStore(pausedStore.subscribe, pausedStore.getSnapshot, pausedStore.getServerSnapshot);
  const tape: [string, string?][] = rows.length
    ? rows.map((r) => [r.asset, num(r.private + r.public, i18n.language)])
    : [[t("tracker-portfolio:ticker.hold")], [t("tracker-portfolio:ticker.links")]];
  const laps = Math.max(2, 2 * Math.ceil(8 / tape.length));
  const label = paused ? t("tracker-portfolio:ticker.play") : t("tracker-portfolio:ticker.pause");
  return (
    <div className="flex items-center overflow-hidden rounded-xl border border-card-border bg-surface-inset">
      <style>{"@keyframes tracker-ticker { to { transform: translateX(-50%); } }"}</style>
      {/* Nothing moves when motion is reduced, so there is nothing to pause. */}
      <button
        type="button"
        aria-pressed={paused}
        aria-label={label}
        title={label}
        onClick={() => pausedStore.set(!paused)}
        className={`ml-2 inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-bitcoin text-black hover:bg-bitcoin-hover motion-reduce:hidden ${FOCUS}`}
      >
        {paused ? <Play size={16} aria-hidden /> : <Pause size={16} aria-hidden />}
      </button>
      <div aria-hidden className="min-w-0 flex-1 overflow-hidden whitespace-nowrap motion-reduce:whitespace-normal">
        <div
          className={`inline-flex py-3 hover:[animation-play-state:paused] motion-safe:animate-[tracker-ticker_40s_linear_infinite] motion-reduce:flex motion-reduce:flex-wrap motion-reduce:gap-y-2 ${
            paused ? "[animation-play-state:paused]" : ""
          }`}
        >
          {Array.from({ length: laps }, (_, lap) =>
            tape.map(([word, figure], i) => (
              <span
                key={`${lap}:${i}`}
                className={`inline-flex items-baseline gap-2.5 px-4 text-sm font-semibold uppercase tracking-wide text-foreground after:ml-5 after:text-bitcoin after:content-['•'] ${
                  lap ? "motion-reduce:hidden" : ""
                }`}
              >
                {word}
                {figure && <i className="num font-normal not-italic normal-case text-muted">{figure}</i>}
              </span>
            )),
          )}
        </div>
      </div>
    </div>
  );
}

function Vault({ side, mine, rows }: { side: "private" | "public"; mine: GraphNode[]; rows: AssetTotal[] }) {
  const { t } = useTranslation();
  const wallets = mine.filter((n) => Boolean(n.public) === (side === "public")).length;
  const assets = rows.filter((a) => a[side]);
  return (
    <div className={CARD}>
      <div className="flex items-center gap-3">
        <Coin public={side === "public"} size={56} />
        <div>
          <h3 className="text-lg font-semibold text-foreground">
            {side === "public" ? t("tracker-portfolio:public") : t("tracker-portfolio:private")}{" "}
            <span className="ml-1 text-sm font-normal text-muted">{t("tracker-portfolio:vault.wallets", { count: wallets })}</span>
          </h3>
          <p className="text-sm text-muted">{side === "public" ? t("tracker-portfolio:vault.public_hint") : t("tracker-portfolio:vault.private_hint")}</p>
        </div>
      </div>
      {assets.length ? (
        <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-1">
          {assets.map((a) => (
            <li key={a.asset}>
              <Amount amount={a[side]} unit={a.asset} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-sm text-muted">{t("tracker-portfolio:vault.empty")}</p>
      )}
    </div>
  );
}

function Assets({ rows, fakes, wallets }: { rows: AssetTotal[]; fakes: AssetTotal[]; wallets: number }) {
  const { t, i18n } = useTranslation();
  const { analysis } = useTracker();
  const fiat = readFiat(analysis.fiat);
  const lang = i18n.language;
  if (!rows.length && !fakes.length)
    return (
      <p className="text-sm text-muted">
        {wallets ? t("tracker-portfolio:assets.empty") : t("tracker-portfolio:assets.start", { tab: t("tracker:view.manage") })}
      </p>
    );
  const columns = [
    t("tracker-portfolio:assets.asset"),
    t("tracker-portfolio:total"),
    t("tracker-portfolio:private"),
    t("tracker-portfolio:public"),
    ...(fiat ? [t("tracker-portfolio:assets.value")] : []),
    t("tracker-portfolio:assets.split"),
    t("tracker-portfolio:assets.chains"),
  ];
  const CELL = "num px-4 py-3 text-right text-foreground";
  return (
    <div className="overflow-x-auto rounded-xl border border-card-border bg-surface-elevated/50">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-card-border">
            {columns.map((c, i) => (
              <th key={i} scope="col" className={`eyebrow px-4 py-3 font-normal ${i === 0 || i >= columns.length - 2 ? "text-left" : "text-right"}`}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...rows, ...fakes].map((a) => {
            const total = a.private + a.public;
            const share = total ? a.private / total : 0;
            const value = fake(a) ? 0 : fiat?.assets[a.asset];
            return (
              <tr key={`${a.asset}${fake(a) ? "|fake" : ""}`} className="border-t border-card-border first:border-t-0">
                <th scope="row" className="px-4 py-3 text-left font-semibold text-foreground">
                  <span className="flex flex-wrap items-center gap-2">
                    {a.asset}
                    {fake(a) && <TrustBadge balance={a} />}
                  </span>
                </th>
                <td className={CELL}>{num(total, lang)}</td>
                <td className={CELL}>{num(a.private, lang)}</td>
                <td className={CELL}>{num(a.public, lang)}</td>
                {fiat && <td className={CELL}>{value == null ? "–" : money(value, fiat.currency, lang)}</td>}
                <td className="px-4 py-3">
                  <span className="block w-24">
                    <Bar label={t("tracker-portfolio:assets.split_label", { private: Math.round(share * 100), public: Math.round(100 - share * 100) })}>
                      <Segment kind="mine" share={share} />
                      <Segment kind="public" share={1 - share} />
                    </Bar>
                  </span>
                </td>
                <td className="px-4 py-3 text-muted">{[...a.chains].join(", ")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** One of my wallets at a glance; the whole card opens it. */
function WalletCard({ v }: { v: Verdict }) {
  const { t } = useTranslation();
  const { analysis, byAddress, openWallet, nameOf } = useTracker();
  const address = v.address;
  const n = byAddress.get(address);
  if (!n) return null;
  const fiat = readFiat(analysis.fiat);
  const origin = analysis.origins[address]?.[0];
  const owned = n.addresses?.length ?? 0;
  const { level, headline } = judge(v, nameOf, t);
  return (
    <li className={`${CARD} relative flex flex-col gap-3 hover:border-hairline-strong`}>
      <div className="flex items-center gap-3">
        <Coin public={n.public} />
        <div className="min-w-0 flex-1">
          <h3>
            {/* The button's box covers the card, so a click anywhere on it opens the wallet. */}
            <button
              type="button"
              onClick={() => openWallet(address)}
              className="text-left font-semibold text-foreground after:absolute after:inset-0 after:rounded-xl focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-bitcoin"
            >
              {title(n)}
            </button>
          </h3>
          <span className="flex items-center gap-1.5 font-mono text-xs text-muted">
            <ChainMark chain={n.chain} size={14} />
            {owned ? t("tracker-portfolio:addresses", { count: owned }) : short(address)}
          </span>
        </div>
        <ChevronRight size={18} aria-hidden className="shrink-0 text-muted" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge public={n.public} />
        <GradeChip address={address} />
        {fiat && v.checked === "none" ? (
          <span className="font-mono text-sm text-muted" title={t("tracker-portfolio:card.unchecked")}>
            –
          </span>
        ) : (
          <Worth value={fiat?.wallets[address]} fiat={fiat} partly={v.checked === "partial"} />
        )}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {held(n)
          .filter((b) => !fake(b))
          .slice(0, 4)
          .map((b) => (
            <Amount key={`${b.chain}:${b.token}:${b.asset}`} amount={b.amount} unit={b.asset} />
          ))}
      </div>
      <VerdictBox level={level} headline={headline} />
      {origin && (
        <div className="text-xs text-muted">
          {t("tracker-portfolio:card.origin", { asset: origin.asset })}
          <OriginBar asset={origin} />
        </div>
      )}
    </li>
  );
}
