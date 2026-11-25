import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { TRACKER_NS } from "@/lib/i18n/tracker";

// The wallet tracker's own namespaces, held to the rules locale-parity.test.ts sets for common.json.
const LOCALES_DIR = join(process.cwd(), "public/locales");
const LANGS = ["en", "es", "pt", "de", "fr", "pl"];
const read = (lang: string, ns: string) => JSON.parse(readFileSync(join(LOCALES_DIR, lang, `${ns}.json`), "utf8")) as Record<string, string>;
const base = (k: string) => k.replace(/_(zero|one|two|few|many|other)$/, "");
const placeholders = (v: string) => [...v.matchAll(/\{\{\s*(\w+)/g)].map((m) => m[1]).sort();

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return name === "__tests__" ? [] : sourceFiles(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

describe.each(TRACKER_NS)("locale %s", (ns) => {
  const en = read("en", ns);

  it("has the same keys in every language", () => {
    for (const lang of LANGS.slice(1)) {
      const other = read(lang, ns);
      const missing = Object.keys(en).filter((k) => !(k in other));
      // Plural forms English lacks (pl _few/_many) are fine when English has the _other form.
      const orphan = Object.keys(other).filter((k) => !(k in en) && !(k.replace(/_(zero|two|few|many)$/, "_other") in en));
      expect(missing, `${lang}/${ns} is missing`).toEqual([]);
      expect(orphan, `${lang}/${ns} has orphans`).toEqual([]);
    }
  });

  it("uses no em dash and only the placeholders English passes", () => {
    const enPlaceholders = new Map(Object.entries(en).map(([k, v]) => [base(k), placeholders(v)]));
    for (const lang of LANGS) {
      for (const [k, v] of Object.entries(read(lang, ns))) {
        expect(v, `${lang}/${ns}/${k}`).not.toMatch(/—/);
        for (const p of placeholders(v)) expect(enPlaceholders.get(base(k)), `${lang}/${ns}/${k} {{${p}}}`).toContain(p);
      }
    }
  });

  it("has every key the code asks for", () => {
    const has = (k: string) => k in en || `${k}_one` in en || `${k}_other` in en;
    const missing = new Set<string>();
    for (const file of sourceFiles(join(process.cwd(), "src"))) {
      for (const [, key] of readFileSync(file, "utf8").matchAll(new RegExp(`["\`]${ns}:([a-zA-Z0-9_.-]+)["\`]`, "g"))) {
        if (key && !has(key)) missing.add(key);
      }
    }
    expect([...missing]).toEqual([]);
  });
});
