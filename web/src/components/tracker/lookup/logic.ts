import { day, num } from "@/lib/tracker/format";
import type { Connection, LookupResult, Resolved } from "@/lib/tracker/types";

type T = (key: string, o?: Record<string, unknown>) => string;

/** A counterparty as lookup.py lists it. */
export type Party = Connection;

/** Only an EVM address needs a question: which chains to read, unless the scanned code named one. */
export const needsChains = (found: Resolved) => found.chain === "evm" && !found.hint && (Boolean(found.needs_key) || found.chains.length > 1);

export const lookupPath = (address: string, chains: string[]) => `lookup/${encodeURIComponent(address)}${chains.length ? `?chains=${chains.join(",")}` : ""}`;

export const explorerHref = (template: string, address: string) => template.replace("{}", encodeURIComponent(address.replace("bitcoincash:", "")));

const sums = (list: { asset: string; amount: number }[], lang: string) => list.map((b) => `${num(b.amount, lang)} ${b.asset}`).join(", ");

/** What went in and out between the looked-up address and a counterparty, and when. */
export function moved(c: Party, t: T, lang: string): string {
  return [
    c.received.length ? t("tracker-lookup:party.in", { sums: sums(c.received, lang) }) : "",
    c.sent.length ? t("tracker-lookup:party.out", { sums: sums(c.sent, lang) }) : "",
    c.count ? t("tracker-lookup:party.transfers", { count: c.count }) : "",
    c.count ? (c.first === c.last ? day(c.first) : t("tracker-lookup:party.span", { from: day(c.first), to: day(c.last) })) : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** What a lookup does not show, so that "nothing found" is not taken for "nothing there". */
export function caveats(v: LookupResult, t: T): string[] {
  return [
    ...v.errors.map((error) => t("tracker-lookup:leaves.error", { error })),
    v.truncated ? t("tracker-lookup:leaves.truncated") : "",
    v.unvouched ? t("tracker-lookup:leaves.unvouched", { count: v.unvouched }) : "",
    v.more ? t("tracker-lookup:leaves.more", { count: v.more }) : "",
    ...v.notes,
    v.mine ? "" : t("tracker-lookup:leaves.direct"),
    t("tracker-lookup:leaves.live"),
  ].filter(Boolean);
}
