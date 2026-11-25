/** Number and date formatting for the wallet tracker. `lang` is the UI language. */

/** Spam tokens come in absurd amounts: those are written 1E38, not with 38 digits. */
export const num = (x: number, lang = "en") =>
  Number(x).toLocaleString(lang, { maximumSignificantDigits: 5, notation: Math.abs(x) >= 1e15 ? "scientific" : "standard" });

/** A Unix time as YYYY-MM-DD, "?" when unknown. */
export const day = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "?");

/** An amount in a currency; a currency code the browser does not know is written after the number. */
export function money(value: number, currency: string, lang = "en") {
  try {
    return new Intl.NumberFormat(lang, { style: "currency", currency, maximumFractionDigits: Math.abs(value) < 100 ? 2 : 0 }).format(value);
  } catch {
    return `${num(value, lang)} ${currency}`;
  }
}
