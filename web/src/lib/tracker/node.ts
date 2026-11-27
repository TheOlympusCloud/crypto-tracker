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

/**
 * How the lite node stands, or null without one. Only a 404 says there is none: the server
 * answers it at once without a node, while one with a node asks it first, which can take
 * seconds while it syncs or starts. A timeout or a 5xx is that node, busy: "down", never a
 * reason to send fees and broadcasts to the public server.
 */
export async function probeLiteNode(signal: AbortSignal): Promise<LiteNodeState | null> {
  let res: Response;
  try {
    res = await fetch(NODE_API, { signal, credentials: "same-origin" });
  } catch {
    return "down";
  }
  if (res.status >= 500) return "down";
  if (!res.ok) return null;
  try {
    const { state } = (await res.json()) as { state?: unknown };
    return typeof state === "string" && STATES.includes(state) ? (state as LiteNodeState) : null;
  } catch {
    return null; // not this app's server (a static host's HTML fallback)
  }
}
