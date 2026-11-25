"use client";

import { useCallback, useEffect, useRef } from "react";
import { api } from "@/lib/tracker/api";
import type { LookupResult, Resolved } from "@/lib/tracker/types";
import { useTracker } from "@/components/tracker/TrackerProvider";
import { lookupPath, needsChains } from "./logic";

interface Box {
  text: string;
  /** The chain mark of `q`, shown while the box still holds it. */
  mark: { q: string; icon: string } | null;
}
let box: Box = { text: "", mark: null };
const listeners = new Set<() => void>();
/** What the lookup box in the header holds: a lookup started anywhere writes its address there. Never kept. */
export const lookupBox = {
  get: () => box,
  set(next: Partial<Box>) {
    box = { ...box, ...next };
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => void listeners.delete(l);
  },
};

let asked = 0; // only the newest lookup may fill the panel

/**
 * Looking up any address, mine or not: `lookUp` from a pasted, typed or scanned line (resolves it,
 * asks for EVM chains when needed); `openLookup` for a known address and chosen EVM chains.
 * Either ends with the address's view in the panel and its connections in the graph.
 */
export function useLookup(): { lookUp: (text: string) => Promise<void>; openLookup: (address: string, chains?: string[]) => Promise<void> } {
  const { panel, setStatus, openPanel, setScope, show, requestFocus } = useTracker();
  const shown = useRef(panel);
  useEffect(() => {
    shown.current = panel;
  });
  // Only the newest lookup may fill the panel, and only while its busy panel is still open.
  const wanted = (mine: number, address: string) =>
    mine === asked && shown.current?.kind === "lookup-busy" && shown.current.address === address;

  const openLookup = useCallback(async (address: string, chains: string[] = []) => {
    const mine = ++asked;
    openPanel({ kind: "lookup-busy", address });
    shown.current = { kind: "lookup-busy", address }; // before the page has drawn it
    try {
      const v = await api<LookupResult>(lookupPath(address, chains));
      if (!wanted(mine, address)) return;
      if (box.text === address && v.icon) lookupBox.set({ mark: { q: address, icon: v.icon } }); // an EVM address now has its chain
      setScope(v);
      show("graph");
      requestFocus(address);
      openPanel({ kind: "lookup" });
    } catch (e) {
      if (!wanted(mine, address)) return;
      openPanel({ kind: "lookup-error", address, chains, message: e instanceof Error ? e.message : String(e) });
    }
  }, [openPanel, setScope, show, requestFocus]);

  const lookUp = useCallback(async (text: string) => {
    setStatus({ text: "", level: "" });
    const found = await api<Resolved>("resolve", { q: text });
    lookupBox.set({ text: found.address, mark: { q: found.address, icon: found.icon } });
    if (needsChains(found)) {
      asked++; // a lookup still reading gives way to the question
      openPanel({ kind: "chains", address: found.address, chains: found.chains, picked: found.chains, missing: found.needs_key ?? "" });
      return;
    }
    await openLookup(found.address, found.hint ? [found.hint] : []);
  }, [setStatus, openPanel, openLookup]);

  return { lookUp, openLookup };
}
