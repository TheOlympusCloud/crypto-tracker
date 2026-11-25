import { TRACKER_EN } from "@/lib/i18n/tracker";

// English t() for the tracker's tests. Kept apart from harness.tsx, which imports react-i18next
// through the views: a mock factory importing it back would never resolve.
const NS = TRACKER_EN as Record<string, Record<string, string>>;

/** English t() over the tracker's namespaces: "ns:key", {{placeholders}}, plurals by count (_one/_other). */
export function t(key: string, o: Record<string, unknown> = {}): string {
  const [ns, k] = key.includes(":") ? (key.split(":", 2) as [string, string]) : ["common", key];
  const table = NS[ns] ?? {};
  const plural = typeof o.count === "number" ? (o.count === 1 ? `${k}_one` : `${k}_other`) : undefined;
  const text = (plural && table[plural]) ?? table[k] ?? (typeof o.defaultValue === "string" ? o.defaultValue : key);
  return text.replace(/\{\{(\w+)\}\}/g, (raw, name: string) => (name in o ? String(o[name]) : raw));
}

export function i18nMock() {
  return { useTranslation: () => ({ t, i18n: { language: "en" } }) };
}
