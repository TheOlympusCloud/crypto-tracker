import { API } from "./api";

/**
 * The tracker server's lite Bitcoin node (the `node-lite` profile): a pruned node with no
 * address index. It answers the scanner's fee estimates, chain tip and broadcasts in mempool's
 * API shapes, so upstream's clients call it unchanged. Address and transaction scans stay on
 * the scanner's own server, and the badge says so (ConnectionBadge).
 */
export const NODE_API = `${API}node`;

export type LiteNodeState = "ready" | "syncing" | "down";

const STATES: readonly string[] = ["ready", "syncing", "down"];

/** How the lite node stands, or null without one (404: no lite node, or not this app's server). */
export async function probeLiteNode(signal: AbortSignal): Promise<LiteNodeState | null> {
  try {
    const res = await fetch(NODE_API, { signal, credentials: "same-origin" });
    if (!res.ok) return null;
    const { state } = (await res.json()) as { state?: unknown };
    return typeof state === "string" && STATES.includes(state) ? (state as LiteNodeState) : null;
  } catch {
    return null;
  }
}
