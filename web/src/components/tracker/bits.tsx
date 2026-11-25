"use client";

import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { usePalette } from "@/hooks/usePalette";
import { day, money, num } from "@/lib/tracker/format";
import { HATCHED, kindColor, trustColor, trustLabelKey, trustShape, type VerdictLevel } from "@/lib/tracker/kinds";
import type { Balance, Fiat, Verdict } from "@/lib/tracker/types";
import { Glyph, VerdictIcon } from "./Glyph";

// Small pieces the tracker's views share. All text reaches the page as text: labels and token
// names come from strangers.

/** A token judged fake (dressed as a real one, or an unasked airdrop): shown, marked, worth 0. */
export const fake = (b: Balance) => b.trust?.level === "scam";
/** What a wallet holds: summed over its addresses when it is an HD wallet. */
export const held = (n: { wallet_balances?: Balance[]; balances?: Balance[] }) => n.wallet_balances ?? n.balances ?? [];
/** The fiat values, when any price is known. */
export const readFiat = (fiat: Fiat | null | undefined) => (fiat?.currency ? fiat : null);

/** An amount and its unit. */
export function Amount({ amount, unit }: { amount: number; unit: string }) {
  const { i18n } = useTranslation();
  return (
    <span className="whitespace-nowrap">
      <span className="font-mono text-foreground">{num(amount, i18n.language)}</span>
      <small className="ml-1.5 text-xs tracking-wide text-muted">{unit}</small>
    </span>
  );
}

/** "public" or "private": whether a wallet of mine is tied to my identity. */
export function Badge({ public: pub }: { public: boolean | number | undefined }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
        pub ? "border-bitcoin/60 bg-bitcoin/10 text-bitcoin" : "border-card-border bg-surface-inset text-foreground"
      }`}
    >
      {pub ? t("tracker:badge.public") : t("tracker:badge.private")}
    </span>
  );
}

/** A tag of the same shape for other states ("watched", "not yours", "sync error"). */
export function Tag({ children, tone = "muted", title }: { children: ReactNode; tone?: "muted" | "danger"; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
        tone === "danger" ? "border-danger/60 bg-danger/10 text-danger" : "border-card-border text-muted"
      }`}
    >
      {children}
    </span>
  );
}

/** A value in the display currency; nothing without one. `partly`: only part was read, so "at least". */
export function Worth({ value, fiat, partly = false }: { value: number | null | undefined; fiat: Pick<Fiat, "currency"> | null; partly?: boolean }) {
  const { t, i18n } = useTranslation();
  if (!fiat || value == null) return null;
  const text = money(value, fiat.currency, i18n.language);
  return (
    <span className="font-mono text-sm text-foreground" title={partly ? t("tracker:worth.partly") : undefined}>
      {partly ? `≥ ${text}` : text}
    </span>
  );
}

/** A token's trust, its reason one tap away. "verified" only means that a list names it. */
export function TrustBadge({ balance }: { balance: Balance }) {
  const { t } = useTranslation();
  const p = usePalette();
  const level = balance.trust?.level ?? "unknown";
  const color = trustColor(level, p);
  return (
    <details className="group ml-auto text-right">
      <summary
        className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider [&::-webkit-details-marker]:hidden"
        style={{ color, borderColor: color, borderStyle: level === "unknown" ? "dashed" : "solid" }}
      >
        <Glyph shape={trustShape(level)} color={color} size={14} />
        {t(trustLabelKey(level))}
      </summary>
      <p className="mt-2 text-left text-sm text-foreground">
        {balance.trust?.reason}
        {balance.name ? ` ${t("tracker:trust.name", { name: balance.name })}` : ""}
        {balance.token && <small className="mt-1 block break-all font-mono text-muted">{balance.token}</small>}
      </p>
    </details>
  );
}

/** Fake tokens, folded away under the rest with their count. */
export function FakeList({ balances }: { balances: Balance[] }) {
  const { t } = useTranslation();
  if (!balances.length) return null;
  return (
    <details className="mt-2 text-sm">
      <summary className="cursor-pointer text-muted">{t("tracker:fakes", { count: balances.length })}</summary>
      <ul className="mt-2 space-y-2">
        {balances.map((b) => (
          <li key={`${b.chain}:${b.token}`} className="flex flex-wrap items-center gap-2">
            <Amount amount={b.amount} unit={`${b.asset} · ${b.chain}`} />
            <TrustBadge balance={b} />
          </li>
        ))}
      </ul>
    </details>
  );
}

/** A verdict at a glance: its glyph and headline, and anything more under it. */
export function VerdictBox({ level, headline, children }: { level: VerdictLevel; headline: string; children?: ReactNode }) {
  const border = { ok: "border-card-border", warn: "border-warning/50", bad: "border-danger/60", unknown: "border-card-border border-dashed" }[level];
  return (
    <div className={`flex items-start gap-2.5 rounded-lg border bg-surface-inset px-3 py-2.5 ${border}`}>
      <VerdictIcon level={level} />
      <span className="min-w-0 text-sm">
        <b className="font-semibold text-foreground">{headline}</b>
        {children}
      </span>
    </div>
  );
}

/** One segment of a split bar: solid, or hatched for the kinds in HATCHED so it reads without colour. */
export function Segment({ kind, share, tip }: { kind: string; share: number; tip?: string }) {
  const p = usePalette();
  const c = kindColor(kind, p);
  const background = HATCHED.has(kind) ? `repeating-linear-gradient(135deg, ${c} 0 4px, color-mix(in srgb, ${c} 35%, transparent) 4px 7px)` : c;
  return <i className="block min-w-0" style={{ width: `${share * 100}%`, background }} title={tip} />;
}

/** A split bar: segments side by side, named as a whole for screen readers. */
export function Bar({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span role="img" aria-label={label} className="my-1 flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-surface-inset">
      {children}
    </span>
  );
}

/** How far back a wallet's history is read: my wallets are read in full over several syncs. */
export function HistoryNote({ verdict }: { verdict: Verdict }) {
  const { t } = useTranslation();
  const c = verdict.coverage;
  if (c.filling != null)
    return <small className="block text-xs text-muted">{c.filling ? t("tracker:history.filling_back", { date: day(c.filling) }) : t("tracker:history.filling")}</small>;
  if (verdict.checked === "full" && !c.truncated) return <small className="block text-xs text-muted">{t("tracker:history.complete")}</small>;
  return null;
}

/** A link to am-i-exposed's analysis of one Bitcoin address, in the same app. */
export function PrivacyScoreLink({ address, chain }: { address: string; chain: string }) {
  const { t } = useTranslation();
  if (chain !== "bitcoin") return null;
  return (
    <a href={`/#addr=${encodeURIComponent(address)}`} className={BUTTON}>
      {t("tracker:privacy_score")}
    </a>
  );
}

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";
/** Class of a secondary button (or a link that looks like one). */
export const BUTTON = `inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg border border-card-border bg-surface-inset px-3 text-sm text-foreground hover:border-hairline-strong ${FOCUS}`;
/** Class of the primary button of a panel or form. */
export const PRIMARY = `inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg bg-bitcoin px-3 text-sm font-semibold text-black hover:bg-bitcoin-hover disabled:opacity-50 ${FOCUS}`;
/** Class of a full-width row that opens something (a wallet, a hop, a counterparty). */
export const ROW = `flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-surface-inset ${FOCUS}`;
/** Class of a card. */
export const CARD = "rounded-xl border border-card-border bg-surface-elevated/50 p-4 sm:p-5";
