"use client";

import type { PanelState } from "@/components/tracker/TrackerProvider";

// Placeholder until the view is ported.
export function LookupPanel(_props: { panel: Exclude<PanelState, { kind: "wallet" | "node" | "path" }> }) {
  return null;
}
