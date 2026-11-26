"use client";

import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { pct } from "@/lib/tracker/kinds";
import type { WalletGroup } from "@/lib/tracker/types";
import { Badge, CARD, PRIMARY, ROW, VerdictBox } from "../bits";
import { ChainMark, Coin } from "../Glyph";
import { useTracker } from "../TrackerProvider";

const H3 = "mt-6 mb-2 text-sm font-semibold text-foreground";

/** The headline of a group: a public wallet in it names everyone, otherwise they look like one person. */
function Headline({ g }: { g: WalletGroup }) {
  const { t } = useTranslation();
  const { nameOf } = useTracker();
  const names = (list: string[]) => list.map(nameOf).join(", ");
  return g.public.length ? (
    <VerdictBox level="bad" headline={t("tracker-portfolio:groups.public", { count: g.public.length, names: names(g.public) })} />
  ) : (
    <VerdictBox level="warn" headline={t("tracker-portfolio:groups.private")} />
  );
}

function Chains({ g }: { g: WalletGroup }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {g.chains.map((c) => (
        <ChainMark key={c} chain={c} size={18} />
      ))}
    </span>
  );
}

/** Links of a wallet still being read are not known yet: the group may be larger. */
function Partly({ g }: { g: WalletGroup }) {
  const { t } = useTranslation();
  return g.checked === "full" ? null : <p className="text-sm text-muted">{t("tracker-portfolio:groups.partial")}</p>;
}

/** Which of my wallets an observer can join into one person: one card per group. */
export function GroupCards() {
  const { t } = useTranslation();
  const { analysis, nameOf, openPanel } = useTracker();
  const groups = analysis.groups ?? [];
  if (!groups.length) return <p className="max-w-[70ch] text-sm text-muted">{t("tracker-portfolio:groups.none")}</p>;
  return (
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {groups.map((g) => {
        const weakest = g.links[0];
        return (
          <li key={g.wallets[0]} className={`${CARD} relative flex flex-col gap-3 hover:border-hairline-strong`}>
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <h3>
                  {/* The button's box covers the card, so a click anywhere on it opens the group. */}
                  <button
                    type="button"
                    onClick={() => openPanel({ kind: "group", address: g.wallets[0]! })}
                    className="text-left font-semibold text-foreground after:absolute after:inset-0 after:rounded-xl focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-bitcoin"
                  >
                    {t("tracker-portfolio:groups.title", { count: g.wallets.length })}
                  </button>
                </h3>
                <p className="mt-1 text-sm text-muted">{g.wallets.map(nameOf).join(", ")}</p>
              </div>
              <ChevronRight size={18} aria-hidden className="shrink-0 text-muted" />
            </div>
            <Chains g={g} />
            <Headline g={g} />
            {weakest && (
              <p className="text-xs text-muted">{t("tracker-portfolio:groups.weakest", { a: nameOf(weakest.a), b: nameOf(weakest.b), pct: pct(weakest.confidence) })}</p>
            )}
            <Partly g={g} />
          </li>
        );
      })}
    </ul>
  );
}

/** One group: its wallets, and the links holding it together, weakest first. */
export function GroupPanel({ address }: { address: string }) {
  const { t } = useTranslation();
  const { analysis, byAddress, nameOf, openWallet, showPath, showGroup } = useTracker();
  const g = analysis.groups?.find((x) => x.wallets.includes(address));
  if (!g) return null;
  const names = (list: string[]) => list.map(nameOf).join(", ");
  return (
    <div className="space-y-3">
      <div className="space-y-1 pr-8">
        <h2 className="break-words text-lg font-semibold text-foreground">{t("tracker-portfolio:groups.title", { count: g.wallets.length })}</h2>
        <Chains g={g} />
      </div>
      <Headline g={g} />
      <Partly g={g} />
      <button type="button" className={PRIMARY} onClick={() => showGroup(address)}>
        {t("tracker-portfolio:wallet.show")}
      </button>

      <h3 className={H3}>{t("tracker-portfolio:groups.wallets")}</h3>
      <ul>
        {g.wallets.map((w) => (
          <li key={w}>
            <button type="button" className={ROW} onClick={() => openWallet(w)}>
              <Coin public={byAddress.get(w)?.public} size={28} />
              <span className="min-w-0 flex-1 break-words text-sm text-foreground">{nameOf(w)}</span>
              <Badge public={byAddress.get(w)?.public} />
            </button>
          </li>
        ))}
      </ul>

      <h3 className={H3}>{t("tracker-portfolio:groups.links")}</h3>
      <ul>
        {g.links.map((k) => (
          <li key={`${k.a}|${k.b}`}>
            <button type="button" className={ROW} onClick={() => showPath(k.path, `${nameOf(k.a)} ↔ ${nameOf(k.b)}`, k.a)}>
              <span className="min-w-0 flex-1 text-sm text-foreground">
                {nameOf(k.a)} ↔ {nameOf(k.b)}
                <small className="block text-xs text-muted">
                  {[t("tracker-portfolio:hops", { count: k.path.length - 1 }), [...new Set(k.kinds)].join(" + ")].join(", ")}
                </small>
                {k.apart && <small className="block text-xs text-warning">{t("tracker-portfolio:groups.alone", { a: names(k.apart[0]), b: names(k.apart[1]) })}</small>}
              </span>
              <span className="num shrink-0 text-sm font-semibold text-warning" title={t("tracker-portfolio:evidence.confidence", { pct: pct(k.confidence) })}>
                {pct(k.confidence)}
                <span className="sr-only"> {t("tracker-portfolio:evidence.confidence_sr")}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
