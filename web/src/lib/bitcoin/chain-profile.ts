import { base58 } from "@scure/base";

/**
 * crypto-tracker: the UTXO chain a scan runs on. The engine reads the active profile instead of
 * Bitcoin constants: heuristics a chain lacks the features for are skipped (`needs` in
 * heuristic-steps.ts), thresholds come from it, and Litecoin, Dogecoin and Bitcoin Cash are read
 * through the tracker's server, which answers Esplora's calls (/api/tracker/utxo/<chain>).
 * One scan runs at a time, so the active chain is module state, set when a scan starts.
 */
export type ChainId = "bitcoin" | "litecoin" | "dogecoin" | "bitcoincash";

export type Feature =
  | "segwit"
  | "taproot"
  | "rbf"
  /** Wallets and protocols that only exist on Bitcoin: Whirlpool, Wasabi, Stonewall, BIP47... */
  | "bitcoin-wallets"
  /** am-i-exposed's index of Bitcoin entities and its OFAC list. */
  | "entity-index"
  /** Fee rates follow demand, so an exact one fingerprints the wallet (Dogecoin wallets pay a fixed rate per kB). */
  | "fee-market";

export interface ChainProfile {
  id: ChainId;
  name: string;
  unit: string;
  blockSeconds: number;
  /** Base units below which an output is dust. */
  dust: number;
  /** Base-unit multiples that make an amount round. */
  roundMultiples: number[];
  features: ReadonlySet<Feature>;
  /** How its addresses look; Bitcoin's are upstream's own patterns (detect-input.ts). */
  address?: { bech32?: string; base58: number[]; cashaddr?: boolean };
  /** Esplora-shaped base URL; Bitcoin's is the backend chosen in the settings. */
  api: string;
  /** Explorer pages, {} standing for the address or txid; Bitcoin's is the backend's. */
  explorer: { address: string; tx: string };
}

const BTC_ROUND = [10_000, 100_000, 1_000_000, 10_000_000];
const DOGE = 100_000_000;

export const CHAINS: Record<ChainId, ChainProfile> = {
  bitcoin: {
    id: "bitcoin",
    name: "Bitcoin",
    unit: "BTC",
    blockSeconds: 600,
    dust: 1000,
    roundMultiples: BTC_ROUND,
    features: new Set(["segwit", "taproot", "rbf", "bitcoin-wallets", "entity-index", "fee-market"]),
    api: "",
    explorer: { address: "https://mempool.space/address/{}", tx: "https://mempool.space/tx/{}" },
  },
  litecoin: {
    id: "litecoin",
    name: "Litecoin",
    unit: "LTC",
    blockSeconds: 150,
    dust: 1000,
    roundMultiples: BTC_ROUND,
    features: new Set(["segwit", "taproot", "rbf", "fee-market"]),
    address: { bech32: "ltc", base58: [0x30, 0x32] },
    api: "/api/tracker/utxo/litecoin",
    explorer: { address: "https://litecoinspace.org/address/{}", tx: "https://litecoinspace.org/tx/{}" },
  },
  dogecoin: {
    id: "dogecoin",
    name: "Dogecoin",
    unit: "DOGE",
    blockSeconds: 60,
    dust: DOGE / 100, // Dogecoin Core's dust limit, 0.01 DOGE
    // Whole coins are the norm on Dogecoin: only large round sums say anything.
    roundMultiples: [1_000 * DOGE, 10_000 * DOGE, 100_000 * DOGE],
    features: new Set(["rbf"]),
    address: { base58: [0x1e, 0x16] },
    api: "/api/tracker/utxo/dogecoin",
    explorer: { address: "https://blockchair.com/dogecoin/address/{}", tx: "https://blockchair.com/dogecoin/transaction/{}" },
  },
  bitcoincash: {
    id: "bitcoincash",
    name: "Bitcoin Cash",
    unit: "BCH",
    blockSeconds: 600,
    dust: 1000,
    roundMultiples: BTC_ROUND,
    features: new Set(["fee-market"]), // no RBF, no segwit
    address: { base58: [], cashaddr: true },
    api: "/api/tracker/utxo/bitcoincash",
    explorer: { address: "https://blockchair.com/bitcoin-cash/address/{}", tx: "https://blockchair.com/bitcoin-cash/transaction/{}" },
  },
};

let active: ChainProfile = CHAINS.bitcoin;

/** The chain of the scan running or shown. */
export const chain = (): ChainProfile => active;
export const setChain = (id: ChainId): void => { active = CHAINS[id]; };
export const has = (feature: Feature): boolean => active.features.has(feature);
export const isChainId = (s: unknown): s is ChainId => typeof s === "string" && Object.hasOwn(CHAINS, s);

const BECH32_DATA = /^[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{39,87}$/;
const CASHADDR = /^(bitcoincash:)?[qp][qpzry9x8gf2tvdw0s3jn54khce6mua7l]{41}$/;

/**
 * Litecoin, Dogecoin or Bitcoin Cash, from an address's format alone, or null (Bitcoin, or no
 * address). Checksums are left to the tracker's server, which refuses a typo. Litecoin's old
 * 3... addresses are Bitcoin's, as in the tracker.
 */
export function otherChain(input: string): ChainId | null {
  const lower = input.toLowerCase();
  let version: number | undefined;
  try {
    const bytes = base58.decode(input);
    if (bytes.length === 25) version = bytes[0];
  } catch {
    // not base58
  }
  for (const p of Object.values(CHAINS)) {
    const a = p.address;
    if (!a) continue;
    if (a.bech32 && lower.startsWith(`${a.bech32}1`) && BECH32_DATA.test(lower.slice(a.bech32.length + 1))) return p.id;
    if (a.cashaddr && CASHADDR.test(lower)) return p.id;
    if (version !== undefined && a.base58.includes(version)) return p.id;
  }
  return null;
}

/**
 * The backend of a scan on chain `id` (the active one by default): Bitcoin's is the one in the
 * settings, the others are the tracker's. Every client of a scan's result page takes it from here.
 */
export const scanConfig = <C extends { mempoolBaseUrl: string }>(config: C, id: ChainId = active.id): C =>
  id === "bitcoin" ? config : { ...config, mempoolBaseUrl: CHAINS[id].api };

/** The scanner's form of an address: Bitcoin Cash's without its prefix. */
export const bareAddress = (address: string): string => address.replace(/^bitcoincash:/i, "");

/** The hash of a scan on the active chain: #tx=<txid>&chain=<chain>, the chain left out on Bitcoin. */
export function scanHash(kind: "tx" | "addr", ref: string, id: ChainId = active.id): string {
  return `${kind}=${encodeURIComponent(ref)}${id === "bitcoin" ? "" : `&chain=${id}`}`;
}

/** A page of the chain's explorer, for chains other than Bitcoin. */
export const explorerPage = (kind: "address" | "tx", ref: string): string =>
  active.explorer[kind].replace("{}", encodeURIComponent(ref));
