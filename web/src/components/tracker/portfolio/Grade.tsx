"use client";

import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";
import { GRADE_BADGE_COLORS, gradeColor } from "@/lib/constants";
import { gapText, word } from "@/lib/tracker/exposure";
import { chainLabel, short } from "@/lib/tracker/kinds";
import type { WalletExposure } from "@/lib/tracker/types";
import { ChainMark } from "../Glyph";
import { Tag } from "../bits";
import { useTracker } from "../TrackerProvider";

/** Chains whose addresses am-i-exposed deep-scans in the browser, apart from the wallet's grade. */
const UTXO = new Set(["bitcoin", "litecoin", "dogecoin", "bitcoincash"]);

/** The privacy score of the wallet whose first address is `address`: null when never read, undefined from a server without scores. */
export function useWalletExposure(address: string): WalletExposure | null | undefined {
  return useTracker().analysis.exposure?.[address];
}

/** A wallet's grade at a glance, in am-i-exposed's grade colours; "–" for a wallet never read. */
export function GradeChip({ address }: { address: string }) {
  const { t } = useTranslation();
  const e = useWalletExposure(address);
  if (e === undefined) return null;
  if (!e)
    return (
      <span className="font-mono text-sm text-muted" title={t("tracker-exposure:wallet.unchecked")}>
        –
      </span>
    );
  const label = t("tracker-exposure:wallet.grade", { grade: e.grade, score: e.score });
  return (
    <span
      title={e.partial ? `${label}. ${t("tracker-exposure:partly")}` : label}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-bold tracking-wider ${GRADE_BADGE_COLORS[e.grade]} ${e.partial ? "border border-dashed border-current" : ""}`}
    >
      <span aria-hidden="true">
        {e.grade} · {e.score}
      </span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** A wallet's grade, score and findings by chain. `chain` is the wallet's: UTXO wallets say their addresses' deep scans are not in it. */
export function WalletGrade({ address, chain }: { address: string; chain: string }) {
  const { t } = useTranslation();
  const e = useWalletExposure(address);
  if (e === undefined) return null;
  if (!e) return <p className="text-sm text-muted">{t("tracker-exposure:wallet.unchecked")}</p>;
  // The wallet's own findings first, then each chain's.
  const groups = Object.entries(e.by_chain).sort(([a], [b]) => (a ? 1 : 0) - (b ? 1 : 0) || a.localeCompare(b));
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`num text-3xl font-bold ${gradeColor(e.grade)}`}>{e.grade}</span>
        <span className="num text-sm text-muted">{t("tracker-exposure:wallet.score", { score: e.score })}</span>
        {e.partial && <Tag>{t("tracker-exposure:partly")}</Tag>}
      </div>
      {UTXO.has(chain) && <p className="mt-1 text-xs text-muted">{t("tracker-exposure:wallet.not_deep")}</p>}
      {e.gaps.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs text-muted">
          {e.gaps.map((g, i) => (
            <li key={i}>{gapText(g, t)}</li>
          ))}
        </ul>
      )}
      {groups.length ? (
        groups.map(([on, found]) => (
          <section key={on} className="mt-3">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {on && (
                <span aria-hidden="true">
                  <ChainMark chain={on} size={14} />
                </span>
              )}
              {on ? chainLabel(on) : t("tracker-exposure:wallet.across")}
            </h4>
            <ul className="mt-1 space-y-1">
              {found.map((f) => (
                <FindingRow key={f.id} f={f} />
              ))}
            </ul>
          </section>
        ))
      ) : (
        <p className="mt-2 text-sm text-muted">{t("tracker-exposure:wallet.none")}</p>
      )}
    </div>
  );
}

/** One finding: its title and impact, the rest one tap away. */
function FindingRow({ f: raw }: { f: WalletExposure["findings"][number] }) {
  const { t } = useTranslation();
  const f = word(raw, t);
  return (
    <li>
      <details className="rounded-lg bg-surface-inset px-3 py-2 text-sm">
        <summary className="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden">
          <span className="min-w-0 flex-1 text-foreground">{f.title}</span>
          <span className="num shrink-0 font-semibold text-muted">{f.scoreImpact}</span>
        </summary>
        <p className="mt-2 text-muted">{f.description}</p>
        <p className="mt-2 text-foreground">{f.recommendation}</p>
        {f.evidence.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {f.evidence.map((e) => (
              <li key={`${e.chain}:${e.txid}`}>
                <a href={e.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 font-mono text-xs text-muted hover:text-foreground">
                  <span aria-hidden="true">
                    <ChainMark chain={e.chain} size={12} />
                  </span>
                  {short(e.txid)}
                  <ExternalLink size={11} aria-hidden="true" />
                </a>
              </li>
            ))}
          </ul>
        )}
      </details>
    </li>
  );
}
