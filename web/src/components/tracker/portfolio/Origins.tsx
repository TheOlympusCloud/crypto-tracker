"use client";

import { useTranslation } from "react-i18next";
import { num } from "@/lib/tracker/format";
import { pct, short, sourceKind, typeOf } from "@/lib/tracker/kinds";
import type { OriginAsset } from "@/lib/tracker/types";
import { Bar, ROW, Segment } from "../bits";
import { KindIcon } from "../Glyph";
import { useTracker } from "../TrackerProvider";

/** The share of an asset that came from sources too small to list: the empty end of its bar. */
export const rest = (asset: OriginAsset) => 1 - asset.sources.reduce((sum, s) => sum + s.share, 0);

/** Where one asset of a wallet came from, as a split bar. */
export function OriginBar({ asset }: { asset: OriginAsset }) {
  const { t } = useTranslation();
  return (
    <Bar label={asset.sources.map((s) => `${pct(s.share)} ${typeOf(s, t)}`).join(", ")}>
      {asset.sources.map((s, i) => (
        <Segment key={i} kind={sourceKind(s)} share={s.share} tip={`${pct(s.share)} ${s.name || short(s.address)}`} />
      ))}
    </Bar>
  );
}

/** Each asset a wallet received, and its sources; a source opens the path its funds took. */
export function OriginRows({ address }: { address: string }) {
  const { t, i18n } = useTranslation();
  const { analysis, nameOf, showPath } = useTracker();
  const assets = analysis.origins[address] ?? [];
  if (!assets.length) return <p className="text-sm text-muted">{t("tracker-portfolio:origin.none")}</p>;
  return assets.map((a) => (
    <div key={`${a.chain}:${a.token}:${a.asset}`} className="mt-4">
      <h4 className="text-sm font-semibold text-foreground">
        {t("tracker-portfolio:origin.received", { amount: num(a.received, i18n.language), asset: a.asset, chain: a.chain })}
      </h4>
      <OriginBar asset={a} />
      <ul className="mt-1">
        {a.sources.map((s, i) => (
          <li key={i}>
            <button
              type="button"
              className={ROW}
              onClick={() => showPath(s.path, t("tracker-portfolio:origin.heading", { asset: a.asset, name: nameOf(address) }), address)}
            >
              <KindIcon kind={sourceKind(s)} size={14} />
              <b className="num w-10 shrink-0 text-sm text-foreground">{pct(s.share)}</b>
              <span className="min-w-0 text-sm text-foreground">
                <span className="break-words">{`${s.name || short(s.address)} · ${typeOf(s, t)}`}</span>
                <small className="block text-xs text-muted">
                  {`${num(s.amount, i18n.language)} ${a.asset} · ${t("tracker-portfolio:hops", { count: s.path.length - 1 })}`}
                  {s.reason ? ` · ${t("tracker-portfolio:origin.stopped", { reason: s.reason })}` : ""}
                </small>
              </span>
            </button>
          </li>
        ))}
        {rest(a) > 0.005 && <li className="px-2 py-1 text-xs text-muted">{t("tracker-portfolio:origin.rest", { pct: pct(rest(a)) })}</li>}
      </ul>
    </div>
  ));
}
