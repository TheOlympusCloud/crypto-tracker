import { day } from "@/lib/tracker/format";
import { chainLabel, pct, short, type VerdictLevel } from "@/lib/tracker/kinds";
import type { Verdict } from "@/lib/tracker/types";

/** `t` from useTranslation, or any function of the same shape. */
export type Translate = (key: string, options?: Record<string, unknown>) => string;

/** The headline of a wallet's verdict. "ok" never means safe, only that nothing was found. */
export function judge(v: Verdict, nameOf: (address: string) => string, t: Translate): { level: VerdictLevel; headline: string } {
  const c = v.coverage;
  if (v.checked === "none")
    return { level: "unknown", headline: t(c.error ? "tracker-portfolio:verdict.unchecked_failed" : "tracker-portfolio:verdict.unchecked") };
  const exposing = v.links.filter((k) => k.public !== v.public);
  const best = (v.public ? v.links : exposing)[0] ?? v.links[0];
  // Only a complete check may say that nothing was found.
  if (!best && v.checked === "full") return { level: "ok", headline: t("tracker-portfolio:verdict.clean") };
  if (!best) {
    const headline =
      c.filling == null
        ? t("tracker-portfolio:verdict.partial_errors")
        : c.filling
          ? t("tracker-portfolio:verdict.partial_filling_back", { date: day(c.filling) })
          : t("tracker-portfolio:verdict.partial_filling");
    return { level: "unknown", headline };
  }
  const o = { name: nameOf(best.wallet), pct: pct(best.confidence) };
  // Below even odds a link is a guess to look at, not a finding (a CoinJoin, a weak match).
  if (best.confidence < 0.5)
    return { level: "warn", headline: t(best.public ? "tracker-portfolio:verdict.weak_public" : "tracker-portfolio:verdict.weak_private", o) };
  if (v.public) return { level: "warn", headline: t("tracker-portfolio:verdict.tied", { count: v.links.length }) };
  if (exposing.length) return { level: "bad", headline: t("tracker-portfolio:verdict.linked_public", o) };
  return { level: "warn", headline: t("tracker-portfolio:verdict.linked_private", o) };
}

/** What the verdict cannot rule out, said plainly. */
export function caveats(v: Verdict, nameOf: (address: string) => string, maxHops: number, t: Translate): string[] {
  const c = v.coverage;
  const out: string[] = [];
  if (c.error) out.push(t("tracker-portfolio:caveat.error", { error: c.error }));
  if (c.filling != null) out.push(t("tracker-portfolio:caveat.filling"));
  else if (c.truncated) out.push(t("tracker-portfolio:caveat.truncated"));
  if (c.counterparties > c.expanded)
    out.push(t("tracker-portfolio:caveat.unexpanded", { count: c.counterparties, left: c.counterparties - c.expanded }));
  for (const s of v.shared_services) out.push(t("tracker-portfolio:caveat.shared", { name: nameOf(s.wallet), services: s.services.join(", ") }));
  for (const b of v.bridged ?? []) {
    const key = b.closed ? "bridged_closed" : b.linked ? "bridged" : "bridged_busy";
    out.push(t(`tracker-portfolio:caveat.${key}`, { address: short(b.address), chain: chainLabel(b.chain), protocol: b.protocol }));
  }
  const exchanges = v.services.filter((s) => s.category === "cex").map((s) => s.name);
  if (exchanges.length && !v.public) out.push(t("tracker-portfolio:caveat.exchange", { names: exchanges.join(", ") }));
  if (v.services.some((s) => s.category === "mixer")) out.push(t("tracker-portfolio:caveat.mixer"));
  out.push(t("tracker-portfolio:caveat.hops", { count: maxHops }));
  out.push(...(c.notes ?? []));
  out.push(t("tracker-portfolio:caveat.invisible"));
  return out;
}
