// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type { MempoolTransaction } from "@/lib/api/types";

const m = vi.hoisted(() => ({ createApiClient: vi.fn(() => ({})) }));
vi.mock("@/lib/api/client", () => ({ createApiClient: m.createApiClient }));
vi.mock("@/hooks/useGraphExpansion", () => ({
  useGraphExpansion: () => ({ rootTxid: null, setRoot: vi.fn(), setRootWithLayers: vi.fn() }),
}));
const BITCOIN = { mempoolBaseUrl: "https://mempool.space/api", explorerUrl: "https://mempool.space", label: "Mainnet" };
vi.mock("@/context/NetworkContext", () => ({ useNetwork: () => ({ network: "mainnet", config: BITCOIN }) }));

import { GraphExplorerPanel } from "../GraphExplorerPanel";
import { setChain } from "@/lib/bitcoin/chain-profile";

const tx = { txid: "a".repeat(64), vin: [], vout: [], status: { confirmed: true } } as unknown as MempoolTransaction;

afterEach(() => { cleanup(); setChain("bitcoin"); m.createApiClient.mockClear(); });

describe("GraphExplorerPanel's expansions (crypto-tracker)", () => {
  it("ask the scanned chain's backend: the tracker for Litecoin, never mempool.space", () => {
    setChain("litecoin");
    render(<GraphExplorerPanel tx={tx} />);
    expect(m.createApiClient).toHaveBeenCalledWith(expect.objectContaining({ mempoolBaseUrl: "/api/tracker/utxo/litecoin" }));
    expect(m.createApiClient).not.toHaveBeenCalledWith(BITCOIN);
  });

  it("ask Bitcoin's backend from the settings on a Bitcoin scan", () => {
    render(<GraphExplorerPanel tx={tx} />);
    expect(m.createApiClient).toHaveBeenCalledWith(BITCOIN);
  });
});
