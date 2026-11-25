import { vi } from "vitest";
import type { LookupResult } from "@/lib/tracker/types";
import type { Party } from "../logic";

// A lookup of a labelled exchange address that dealt with one of the demo wallets (Hot wallet).
const parties: Party[] = [
  {
    address: "0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
    mine: 1,
    public: 0,
    name: "Hot wallet",
    chain: "evm",
    count: 3,
    first: 1700000000,
    last: 1710000000,
    sent: [{ chain: "ethereum", asset: "ETH", amount: 0.5 }],
    received: [],
    guess: { kind: "exchange-flow", confidence: 0.6 },
  },
  {
    address: "0x722122df12d4e14e13ac3b6895a86e84145b6967",
    mine: 0,
    public: 0,
    name: "",
    label: "Tornado Cash Router",
    category: "mixer",
    chain: "evm",
    count: 1,
    first: 1700000000,
    last: 1700000000,
    sent: [],
    received: [{ chain: "ethereum", asset: "ETH", amount: 10 }],
  },
];

export const LOOKUP: LookupResult = {
  address: "0x28c6c06298d514db089934071355e5743bf21d60",
  mine: 0,
  public: 0,
  name: "",
  label: "Binance 14",
  category: "cex",
  chain: "evm",
  icon: "ethereum",
  explorer_address: "https://etherscan.io/address/{}",
  chains: ["ethereum"],
  watched: false,
  assets: [
    { chain: "ethereum", asset: "ETH", token: "", amount: 1.5, value: 4500, trust: { level: "verified", reason: "The chain's own coin." } },
    { chain: "ethereum", asset: "PEPE", token: "0xabc", amount: 1000, value: null, trust: { level: "unknown", reason: "No token list names it." } },
    { chain: "ethereum", asset: "USDC", token: "0xdead", name: "USD Coin", amount: 5000, value: null, trust: { level: "scam", reason: "Dressed as USDC." } },
  ],
  value: 4500,
  currency: "USD",
  unvouched: 2,
  risk: {
    level: "warn",
    headline: "Be careful",
    reason: "An exchange's hot wallet.",
    reasons: [
      { level: "warn", text: "An exchange's hot wallet." },
      { level: "info", text: "Active since 2017." },
    ],
  },
  connections: parties,
  more: 4,
  truncated: false,
  errors: [],
  notes: ["Ethereum: only the 10000 newest transfers are read."],
  graph: { nodes: [], edges: [], links: [] },
};

/** A fetch that answers each API path (without /api/tracker/) from `routes`; anything else is a 404. */
export function stubApi(routes: Record<string, unknown | ((body: unknown) => unknown)>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("/api/tracker/", "");
    const key = init?.method === "DELETE" ? `DELETE ${path}` : path;
    if (!(key in routes)) return new Response(JSON.stringify({ detail: `no route ${key}` }), { status: 404 });
    const route = routes[key];
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    const answer = typeof route === "function" ? await (route as (b: unknown) => unknown)(body) : route;
    if (answer instanceof Error) return new Response(JSON.stringify({ detail: answer.message }), { status: 422 });
    return new Response(JSON.stringify(answer), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
