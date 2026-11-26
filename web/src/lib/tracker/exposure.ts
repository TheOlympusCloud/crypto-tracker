import { base58 } from "@scure/base";
import type { FindingId } from "@/lib/analysis/finding-metadata";
import type { ScoringResult } from "@/lib/types";
import type { Exposure, Gap, ServerFinding, TrackerFinding } from "./types";
import { bareAddress, isChainId, scanHash } from "@/lib/bitcoin/chain-profile";

type T = (key: string, o?: Record<string, unknown>) => string;

/** Chains whose privacy scan is the tracker's; Bitcoin's is am-i-exposed's own. */
export const ACCOUNT_CHAINS = ["evm", "solana", "tron"];

/**
 * The account chain an address of am-i-exposed's input box belongs to, from its format alone, or null.
 * Checksums are left to the tracker's server, which refuses a typo.
 */
export function accountChain(input: string): "evm" | "solana" | "tron" | null {
  if (/^0x[0-9a-fA-F]{40}$/.test(input)) return "evm";
  try {
    const bytes = base58.decode(input);
    if (bytes.length === 32) return "solana"; // a bare public key
    if (bytes.length === 25 && bytes[0] === 0x41) return "tron";
  } catch {
    // not base58
  }
  return null;
}

/**
 * Where "Privacy score" goes for an address and its chain mark: am-i-exposed's scan for Bitcoin,
 * Litecoin, Dogecoin and Bitcoin Cash (with the chain's profile), the tracker's for EVM, Solana and
 * Tron (an EVM chain's mark names that chain).
 */
export function scanHref(address: string, chain: string): string | null {
  if (isChainId(chain)) return `/#${scanHash("addr", bareAddress(address), chain)}`;
  if (!chain) return null;
  const evm = ACCOUNT_CHAINS.includes(chain) ? "" : `&chains=${encodeURIComponent(chain)}`;
  return `/tracker/scan/#addr=${encodeURIComponent(address)}${evm}`;
}

/** The tracker's scan of what am-i-exposed's input calls invalid, when it is an EVM, Solana or Tron address. */
export function accountScanHref(input: string): string | null {
  const chain = accountChain(input);
  return chain && scanHref(input, chain);
}

/** The scan page's hash: #addr=<address>&chains=<chain,chain>. */
export function readScanHash(hash: string): { address: string; chains: string[] } {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  return { address: p.get("addr") ?? "", chains: (p.get("chains") ?? "").split(",").filter(Boolean) };
}

export const exposurePath = (address: string, chains: string[]) =>
  `exposure/${encodeURIComponent(address)}${chains.length ? `?chains=${chains.join(",")}` : ""}`;

/** A server finding as upstream's Finding: worded from the tracker-exposure namespace. */
export function word(f: ServerFinding, t: T): TrackerFinding {
  const say = (field: string) => t(`tracker-exposure:finding.${f.id}.${field}`, f.params);
  return { ...f, id: f.id as FindingId, title: say("title"), description: say("description"), recommendation: say("recommendation") };
}

/** The scan as upstream's components take it: each finding worded from the tracker-exposure namespace. */
export function toResult(e: Exposure, t: T): ScoringResult & { findings: TrackerFinding[] } {
  return { score: e.score, grade: e.grade, findings: e.findings.map((f) => word(f, t)), partial: e.partial };
}

/** Why a scan is partly checked, in words. */
export const gapText = (g: Gap, t: T) => t(`tracker-exposure:gap.${g.id}`, g.params);
