"use client";

import { lazy, Suspense, useRef, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { ScanLine, Search } from "lucide-react";
import { api } from "@/lib/tracker/api";
import type { Resolved } from "@/lib/tracker/types";
import { useTracker } from "@/components/tracker/TrackerProvider";
import { ChainMark } from "@/components/tracker/Glyph";
import { lookupBox, useLookup } from "./useLookup";

const QrScanner = lazy(() => import("@/components/QrScanner").then((m) => ({ default: m.QrScanner })));

const ICON_BUTTON =
  "inline-flex size-10 shrink-0 items-center justify-center rounded-md text-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";

/** Always at hand in the header: paste, type or scan any address, mine or not. */
export function LookupBox() {
  const { t } = useTranslation();
  const { fail } = useTracker();
  const { lookUp } = useLookup();
  const { text, mark } = useSyncExternalStore(lookupBox.subscribe, lookupBox.get, lookupBox.get);
  const [scanning, setScanning] = useState(false);
  const typing = useRef<ReturnType<typeof setTimeout>>(undefined);

  // The chain's mark in the box, as soon as what is typed or pasted reads as an address.
  const type = (value: string) => {
    lookupBox.set({ text: value });
    clearTimeout(typing.current);
    const q = value.trim();
    if (!q) return;
    typing.current = setTimeout(() => {
      api<Resolved>("resolve", { q }).then(
        (f) => lookupBox.get().text.trim() === q && lookupBox.set({ mark: { q, icon: f.icon } }),
        () => {},
      );
    }, 300);
  };
  // The scanned text is not shown as it is: a QR code can hold anything, a private key too.
  const scanned = (code: string) => {
    lookupBox.set({ text: "" });
    lookUp(code).catch(fail);
  };
  const label = t("tracker-lookup:box.label");
  const scanLabel = t("tracker-lookup:box.scan");
  const findLabel = t("tracker-lookup:box.find");

  return (
    <>
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const q = text.trim();
          if (q) lookUp(q).catch(fail);
        }}
        className="flex min-w-0 max-w-md flex-1 items-center rounded-lg border border-card-border bg-surface-inset pl-1"
      >
        <span aria-live="polite" className="flex shrink-0 pl-2 empty:hidden">
          {mark && mark.q === text.trim() && <ChainMark chain={mark.icon} size={20} />}
        </span>
        <input
          name="q"
          value={text}
          onChange={(e) => type(e.target.value)}
          aria-label={label}
          placeholder={label}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          enterKeyHint="search"
          className="min-h-10 min-w-0 flex-1 rounded-md bg-transparent px-2 font-mono text-sm text-foreground placeholder:font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-bitcoin/50"
        />
        <button type="button" onClick={() => setScanning(true)} aria-label={scanLabel} title={scanLabel} className={ICON_BUTTON}>
          <ScanLine size={18} aria-hidden="true" />
        </button>
        <button type="submit" aria-label={findLabel} title={findLabel} className={ICON_BUTTON}>
          <Search size={18} aria-hidden="true" />
        </button>
      </form>
      {scanning && (
        <Suspense fallback={null}>
          <QrScanner onResult={scanned} onClose={() => setScanning(false)} />
        </Suspense>
      )}
    </>
  );
}
