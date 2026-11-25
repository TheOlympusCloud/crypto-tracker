"use client";

import { useCallback } from "react";

/**
 * Looking up any address, mine or not: `lookUp` from a pasted, typed or scanned line (resolves it,
 * asks for EVM chains when needed); `openLookup` for a known address and chosen EVM chains.
 * Either ends with the address's view in the panel and its connections in the graph.
 */
export function useLookup(): { lookUp: (text: string) => Promise<void>; openLookup: (address: string, chains?: string[]) => Promise<void> } {
  // Placeholder until the lookup is ported.
  const lookUp = useCallback(async (_text: string) => {}, []);
  const openLookup = useCallback(async (_address: string, _chains: string[] = []) => {}, []);
  return { lookUp, openLookup };
}
