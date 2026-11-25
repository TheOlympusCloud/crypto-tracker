/**
 * Shapes of the wallet tracker's API (the Python server behind the app, under /api/tracker/).
 * Field names are the server's; see tracker/graph.py, lookup.py, prices.py, sync.py.
 */

import type { Finding, Grade } from "@/lib/types";

export type TrustLevel = "verified" | "unknown" | "scam";

/** A token or coin held, as the graph and the lookup list it. */
export interface Balance {
  chain: string;
  asset: string;
  amount: number;
  /** Contract or mint; empty for the native coin. */
  token: string;
  name?: string;
  trust?: { level: TrustLevel; reason: string };
  /** Value in the display currency, lookups only; null without a reliable price. */
  value?: number | null;
}

/** An address in the graph: one of mine, a counterparty, or a labelled service. */
export interface GraphNode {
  address: string;
  mine: number;
  public: number | boolean;
  name: string;
  chain?: string;
  /** The chain mark to show where the server narrowed an EVM address to one chain. */
  icon?: string;
  synced_at?: number | null;
  /** Too much history to read in full (an exchange, a busy service). */
  hub?: number;
  error?: string | null;
  /** The HD wallet ("hd:...") this address belongs to. */
  wallet?: string | null;
  cut?: number;
  explorer_address?: string;
  explorer_tx?: string;
  balances?: Balance[];
  /** An HD wallet's addresses, each with what it holds. */
  addresses?: { address: string; balances?: Balance[] }[];
  /** What an HD wallet holds, summed over its addresses. */
  wallet_balances?: Balance[];
  label?: string;
  category?: string;
  label_type?: string;
  label_source?: "user" | "dataset" | "protocol" | "explorer" | "heuristic";
  label_confidence?: number;
  label_guess?: boolean;
  /** Set by fold(): how many counterparties are folded into this node. */
  folded?: number;
}

/** Transfers between two addresses of one asset on one chain, summed. */
export interface GraphEdge {
  chain: string;
  src: string;
  dst: string;
  asset: string;
  token: string;
  count: number;
  amount: number;
  first: number;
  last: number;
  txid?: string;
  coinjoin?: number;
}

/** A guess that two addresses share an owner. */
export interface GraphLink {
  a: string;
  b: string;
  kind: string;
  confidence: number;
  evidence?: string[];
  count: number;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  links: GraphLink[];
}

/** One way a wallet ties to another of mine. */
export interface VerdictLink {
  wallet: string;
  wallet_id: string;
  public: boolean;
  confidence: number;
  path: string[];
  kinds: string[];
  /** My own wallets the link passes through. */
  via: string[];
}

export interface Coverage {
  synced: boolean;
  truncated: boolean;
  /** Unix time the history is read back to while it is still filling in; null when complete. */
  filling?: number | null;
  error: string | null;
  counterparties: number;
  expanded: number;
  notes: string[];
}

export interface Verdict {
  address: string;
  wallet: string;
  addresses: string[];
  checked: "none" | "partial" | "full";
  name: string;
  public: boolean;
  links: VerdictLink[];
  services: { name: string; category: string; guess: boolean }[];
  shared_services: { wallet: string; services: string[] }[];
  /** Bridged to an address that is not a wallet of mine (tracker/bridges.py). */
  bridged?: BridgedTo[];
  coverage: Coverage;
}

export interface BridgedTo {
  protocol: string;
  address: string;
  chain: string;
  /** Linked to the wallet: read first, and quiet. Not linked: busy, or a service. */
  linked: boolean;
  /** A Solana token account closed since: its owner can no longer be read. */
  closed: boolean;
}

export interface OriginSource {
  address: string;
  name: string;
  /** A label category, "own" for one of my wallets, or where the trace stopped. */
  kind: string;
  guess: boolean;
  type: string;
  public: boolean;
  /** Why the trace went no further, when it stopped short of a label. */
  reason: string;
  share: number;
  amount: number;
  path: string[];
}

export interface OriginAsset {
  chain: string;
  asset: string;
  token: string;
  received: number;
  sources: OriginSource[];
}

export interface ChainCoverage {
  chain: string;
  asset: string;
  status: string;
  note: string;
  name: string;
  explorer_address?: string;
  explorer_tx?: string;
}

/** Fiat values in the display currency; null before any price is known. */
export interface Fiat {
  currency: string;
  total: number | null;
  private: number | null;
  public: number | null;
  unpriced: string[];
  priced_at: number | null;
  assets: Record<string, number>;
  /** Value per wallet, keyed by the wallet's first address. */
  wallets: Record<string, number>;
}

export interface Alert {
  time: number;
  /** A link alert's text; empty for a grade alert, which the page words from `params`. */
  text: string;
  address: string;
  seen: boolean;
  kind?: "link" | "grade";
  params?: { from: Grade; to: Grade; score: number } | null;
}

export interface Alerts {
  unread: number;
  items: Alert[];
}

export interface Watched {
  address: string;
  label: string | null;
  category: string | null;
  chain: string;
}

export interface Analysis {
  max_hops: number;
  hop_decay: number;
  chains: ChainCoverage[];
  fiat: Fiat | null;
  alerts: Alerts;
  watched: Watched[];
  verdicts: Verdict[];
  origins: Record<string, OriginAsset[]>;
  /** Each wallet's privacy score, by its first address; null for a wallet never read. */
  exposure?: Record<string, WalletExposure | null>;
}

export interface SyncStatus {
  running: boolean;
  done: number;
  total: number;
  /** The address being read, or a phase: labels, swaps, prices, tokens, waiting. */
  current: string;
  error: string;
  notes: string[];
  /** The address whose older history is being read between syncs. */
  filling: string;
}

/** What a pasted line or a scanned code names (POST resolve). */
export interface Resolved {
  address: string;
  chain: string;
  /** For an EVM address: the chains this server can read, to choose from. */
  chains: string[];
  /** The EVM chain a payment URI names, when this server reads it. */
  hint: string | null;
  /** The EVM chain a payment URI names that this server cannot read yet. */
  needs_key: string | null;
  icon: string;
}

export type RiskLevel = "bad" | "warn" | "unknown" | "ok";

export interface Risk {
  level: RiskLevel;
  headline: string;
  reason: string;
  reasons: { level: RiskLevel | "info"; text: string }[];
}

/** A counterparty of a looked-up address. */
export interface Connection extends GraphNode {
  count: number;
  /** When the looked-up address first and last dealt with it. */
  first: number;
  last: number;
  /** The likeliest guess that it shares an owner with the looked-up address. */
  guess?: { kind: string; confidence: number };
  sent: { chain: string; asset: string; amount: number }[];
  received: { chain: string; asset: string; amount: number }[];
}

/** Read-only view of any address (GET lookup/<address>). */
export interface LookupResult extends Omit<GraphNode, "balances"> {
  chains: string[];
  watched: boolean;
  assets: Balance[];
  value: number | null;
  currency: string;
  /** Tokens without a reliable price, whose transfers make no counterparties. */
  unvouched: number;
  risk: Risk;
  connections: Connection[];
  more: number;
  truncated: boolean;
  errors: string[];
  notes: string[];
  graph: Graph;
}

export interface PortfolioAsset {
  asset: string;
  verified: boolean;
  fake: boolean;
  chains: string[];
  amount: number;
  private: number;
  public: number;
  price: number | null;
  value: number | null;
  private_value: number | null;
  public_value: number | null;
}

/** A transaction a finding rests on, with its page at the chain's explorer. */
export interface Evidence {
  chain: string;
  txid: string;
  url: string;
}

/** A finding as the server sends it (tracker/exposure): ids and parameters, never sentences. */
export type ServerFinding = Omit<Finding, "title" | "description" | "recommendation" | "id"> & { id: string; evidence: Evidence[] };

/** am-i-exposed's Finding, worded by the adapter, with the transactions it rests on (which upstream's components ignore). */
export type TrackerFinding = Finding & { evidence: Evidence[] };

/** Why a scan is partly checked: an indexer error, a read cut to the newest transactions, a check out of calls. */
export interface Gap {
  id: "error" | "truncated" | "skipped";
  params?: { error?: string; check?: string };
}

/** The privacy scan of an EVM, Solana or Tron address (GET exposure/<address>), in ScoringResult's shape. */
export interface Exposure {
  address: string;
  chain: string;
  chains: string[];
  icon: string;
  score: number;
  grade: Grade;
  partial: boolean;
  findings: ServerFinding[];
  gaps: Gap[];
  explorer_address: string;
}

/**
 * One privacy score per wallet of mine (tracker/exposure/wallets.py), in ScoringResult's shape: the
 * findings counted once each, and every finding by the chain it was found on ("" for the wallet's own).
 */
export interface WalletExposure {
  score: number;
  grade: Grade;
  partial: boolean;
  findings: ServerFinding[];
  by_chain: Record<string, ServerFinding[]>;
  gaps: Gap[];
}
