import type { Balance, GraphNode } from "@/lib/tracker/types";
import { fake } from "../bits";

export interface AssetTotal extends Balance {
  private: number;
  public: number;
  chains: Set<string>;
}

/** What my wallets hold per asset, by name: real assets first, then fakes, never summed into the real one. */
export function assetTotals(mine: GraphNode[]): { rows: AssetTotal[]; fakes: AssetTotal[] } {
  const assets = new Map<string, AssetTotal>();
  for (const n of mine)
    for (const b of n.balances ?? []) {
      const key = `${b.asset}${fake(b) ? "|fake" : ""}`; // a fake USDC never joins the real one
      const a = assets.get(key) ?? { ...b, private: 0, public: 0, chains: new Set<string>() };
      a[n.public ? "public" : "private"] += b.amount;
      a.chains.add(b.chain);
      assets.set(key, a);
    }
  const sorted = [...assets.values()].sort((a, b) => a.asset.localeCompare(b.asset));
  return { rows: sorted.filter((a) => !fake(a)), fakes: sorted.filter(fake) };
}
