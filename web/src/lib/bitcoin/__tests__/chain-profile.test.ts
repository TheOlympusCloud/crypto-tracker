import { afterEach, describe, expect, it, vi } from "vitest";
import { CHAINS, otherChain, scanHash, setChain, type ChainId } from "../chain-profile";
import { cleanInput, detectInputType } from "@/lib/analysis/detect-input";
import { getAddressHeuristicSteps, getTxHeuristicSteps } from "@/lib/analysis/heuristic-steps";
import { runTxHeuristics } from "@/lib/analysis/tx-pipeline";
import { runAddressAnalysis } from "@/lib/analysis/run-address-analysis";
import { runTxidAnalysis } from "@/lib/analysis/run-txid-analysis";
import { buildResultViewModel } from "@/lib/view/tx-view-model";
import { ApiError } from "@/lib/api/fetch-with-retry";
import { formatBtc, formatSats } from "@/lib/format";
import type { ApiClient } from "@/lib/api/client";
import type { MempoolTransaction } from "@/lib/api/types";
import recorded from "./fixtures/utxo-chains.json";

// Trace and Boltzmann are not what is checked here; the trace's own tests cover it.
vi.mock("@/lib/analysis/chain-trace", async (orig) => ({
  ...(await orig<typeof import("@/lib/analysis/chain-trace")>()),
  runChainTrace: async () => ({ backwardLayers: [], forwardLayers: [], backwardFailed: false, forwardFailed: false }),
}));
vi.mock("@/lib/analysis/boltzmann-compute", () => ({ isAutoComputable: () => false, computeBoltzmann: async () => null }));

/**
 * Recorded on 2026-11-25: litecoinspace.org's answers for a Litecoin address and transaction, and
 * the tracker's answers (/api/tracker/utxo/dogecoin, tests/test_utxo.py) for BlockCypher's.
 */
const LTC = recorded.litecoin;
const DOGE = recorded.dogecoin;
const LTC_ADDRESS = LTC.address.address;
const LTC_TX = LTC.tx as unknown as MempoolTransaction;
const DOGE_TX = DOGE.tx as unknown as MempoolTransaction;
const BCH = "qpc2hdexmqwy06jzvdxddkazyp500kqf9vvmvgxnqm";

afterEach(() => setChain("bitcoin"));

const notFound = async () => { throw new ApiError("NOT_FOUND", "nf"); };
const refused = async () => { throw new ApiError("RATE_LIMITED", "429"); };

/** An API answering from the recorded data; anything else is unknown to it. */
function api(data: typeof LTC | typeof DOGE, overrides: Partial<Record<keyof ApiClient, unknown>> = {}): ApiClient {
  const tx = data.tx as unknown as MempoolTransaction;
  return {
    getAddress: async (a: string) => (a === data.address.address ? data.address : notFound()),
    getAddressTxs: async () => data.txs,
    getAddressUtxos: async () => data.utxo,
    getTransaction: async (id: string) => (id === tx.txid ? tx : notFound()),
    getTxOutspends: async () => ("outspends" in data ? data.outspends : tx.vout.map(() => ({ spent: false }))),
    getHistoricalPrice: notFound, // the tracker does not answer price calls
    getHistoricalEurPrice: notFound,
    ...overrides,
  } as unknown as ApiClient;
}

const scanAddress = (a: ApiClient, address: string) =>
  runAddressAnalysis(address, { api: a, controller: new AbortController(), onStep: () => {}, setState: () => {}, t: (k) => k });

const scanTx = (a: ApiClient, txid: string) =>
  runTxidAnalysis(txid, {
    api: a,
    controller: new AbortController(),
    network: "mainnet",
    isCustomApi: false,
    analysisSettingsForCache: { maxDepth: 0, minSats: 1000, skipLargeClusters: false, skipCoinJoins: false, timeout: 30, walletGapLimit: 5, enableCache: true, boltzmannTimeout: 300 },
    onStep: () => {},
    setState: () => {},
  });

const ids = (r: { findings: { id: string }[] } | null) => (r?.findings ?? []).map((f) => f.id);

describe("which chain an input is", () => {
  it("knows Litecoin, Dogecoin and Bitcoin Cash addresses by their format", () => {
    const cases: [string, ChainId | null][] = [
      [LTC_ADDRESS, "litecoin"],
      ["LaMT348PWRnrqeeWArpwQPbuanpXDZGEUz", "litecoin"],
      ["MQMcJhpWHYVeQArcZR3sBgyPZxxRtnH441", "litecoin"],
      ["DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z", "dogecoin"],
      ["A9adXhg8XH3fSziwQaee164B28nom8zXgP", "dogecoin"],
      [BCH, "bitcoincash"],
      [`bitcoincash:${BCH}`, "bitcoincash"],
      // Bitcoin's own, including Litecoin's old 3... form, and anything else
      ["1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", null],
      ["3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", null],
      ["bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", null],
      ["0x" + "a".repeat(40), null],
      ["ltc1qshort", null],
      ["hello", null],
    ];
    for (const [input, want] of cases) expect(otherChain(input), input).toBe(want);
  });

  it("lets them through the scanner's input as addresses, URIs and prefix stripped", () => {
    for (const a of [LTC_ADDRESS, "DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z", BCH]) expect(detectInputType(a), a).toBe("address");
    expect(cleanInput(`litecoin:${LTC_ADDRESS}?amount=1`)).toBe(LTC_ADDRESS);
    expect(cleanInput("dogecoin:DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z")).toBe("DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z");
    expect(cleanInput(`bitcoincash:${BCH}`)).toBe(BCH);
    expect(detectInputType("ltc1qshort")).toBe("invalid");
  });

  it("keeps the chain in the hash of a scan, Bitcoin's without it", () => {
    expect(scanHash("tx", "ab", "bitcoin")).toBe("tx=ab");
    setChain("litecoin");
    expect(scanHash("tx", "ab")).toBe("tx=ab&chain=litecoin");
    expect(scanHash("addr", BCH, "bitcoincash")).toBe(`addr=${BCH}&chain=bitcoincash`);
  });
});

describe("the chain profile", () => {
  it("skips the heuristics a chain has nothing for", () => {
    const steps = () => [...getTxHeuristicSteps(), ...getAddressHeuristicSteps()].map((s) => s.id);
    const all = steps();
    for (const id of ["h11", "tx0", "bip47", "postmix", "ricochet", "entity", "witness", "h10"]) expect(all).toContain(id);
    setChain("litecoin");
    expect(all.filter((id) => !steps().includes(id))).toEqual(["h11", "tx0", "bip47", "postmix", "entity", "ricochet"]);
    for (const id of ["dogecoin", "bitcoincash"] as const) {
      setChain(id);
      expect(all.filter((s) => !steps().includes(s))).toEqual(["h11", "tx0", "bip47", "witness", "postmix", "entity", "ricochet", "h10"]);
    }
  });

  it("never says RBF on Bitcoin Cash, which has none", () => {
    // A recorded transaction, its inputs given Bitcoin's RBF sequence, read as Bitcoin and as Bitcoin Cash
    const tx = { ...DOGE_TX, vin: DOGE_TX.vin.map((v) => ({ ...v, sequence: 0xfffffffd })) };
    expect(ids({ findings: runTxHeuristics(tx) })).toContain("h6-rbf-signaled");
    setChain("bitcoincash");
    expect(ids({ findings: runTxHeuristics(tx) })).not.toContain("h6-rbf-signaled");
  });

  it("shows amounts in the chain's unit", () => {
    expect([formatBtc(150_000_000), formatSats(1234)]).toEqual(["1.5 BTC", "1,234 sats"]);
    setChain("dogecoin");
    expect([formatBtc(150_000_000), formatSats(1234)]).toEqual(["1.5 DOGE", "0.00001234 DOGE"]);
  });

  it("reads Litecoin, Dogecoin and Bitcoin Cash through the tracker, Bitcoin through its own backend", () => {
    expect(CHAINS.bitcoin.api).toBe("");
    expect([CHAINS.litecoin.api, CHAINS.dogecoin.api, CHAINS.bitcoincash.api]).toEqual(
      ["/api/tracker/utxo/litecoin", "/api/tracker/utxo/dogecoin", "/api/tracker/utxo/bitcoincash"],
    );
  });
});

describe("a Litecoin scan", () => {
  it("scans an address with the Litecoin profile", async () => {
    setChain("litecoin");
    const { result } = await scanAddress(api(LTC), LTC_ADDRESS);
    // Received 221 times: reuse makes it F, as on Bitcoin; its type is read from Litecoin's prefix
    expect(ids(result)).toEqual(expect.arrayContaining(["h8-address-reuse", "h10-p2wpkh", "partial-history-partial"]));
    expect(result?.grade).toBe("F");
  });

  it("scans a transaction with the Litecoin profile: no Bitcoin wallet fingerprints", async () => {
    const bitcoin = await scanTx(api(LTC), LTC_TX.txid);
    setChain("litecoin");
    const litecoin = await scanTx(api(LTC), LTC_TX.txid);
    expect(ids(bitcoin.result)).toContain("h11-wallet-fingerprint");
    expect(ids(litecoin.result)).toEqual(expect.arrayContaining(["h6-rbf-signaled", "h3-single-input", "h5-low-entropy"]));
    expect(ids(litecoin.result).filter((id) => id.startsWith("h11-") || id.startsWith("behavioral-"))).toEqual([]);
    expect(litecoin.result.score).toBeGreaterThan(bitcoin.result.score);
    expect(litecoin.result.partial).toBeFalsy(); // a price the tracker does not serve is no gap
  });
});

describe("Dogecoin's hourly quota", () => {
  it("leaves an address scan partly checked, not failed", async () => {
    setChain("dogecoin");
    const { result } = await scanAddress(api(DOGE, { getAddressUtxos: refused }), DOGE.address.address);
    const vm = buildResultViewModel({ result: result!, baseScore: 93 });
    // VerdictBand says "Some data could not be fetched" for these notices
    expect(vm.status.map((f) => f.id)).toEqual(["partial-history-partial", "address-utxos-unavailable"]);
    // The stats say it has spent, though none of the 50 newest transactions shows it
    expect(ids(result)).not.toContain("spending-never-spent");
  });

  it("leaves a transaction scan partly checked, not failed", async () => {
    setChain("dogecoin");
    const { result } = await scanTx(api(DOGE, { getTxOutspends: refused, getAddress: refused }), DOGE_TX.txid);
    expect(result.partial).toBe(true);
    expect(ids(result)).toContain("analysis-incomplete");
  });
});
