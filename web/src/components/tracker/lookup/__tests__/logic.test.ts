import { describe, it, expect } from "vitest";
import { t } from "@/components/tracker/__tests__/i18n-mock";
import { caveats, explorerHref, lookupPath, moved, needsChains, type Party } from "../logic";
import { LOOKUP } from "./fixture";

const evm = { address: "0xd8da6bf26964af9d7eed9e03e53415d37aa96045", chain: "evm", chains: ["ethereum", "avalanche"], hint: null, needs_key: null, icon: "evm" };

describe("needsChains", () => {
  it("asks only for an EVM address readable on several chains, or named for a chain this server cannot read", () => {
    expect(needsChains(evm)).toBe(true);
    expect(needsChains({ ...evm, chains: ["ethereum"] })).toBe(false);
    expect(needsChains({ ...evm, chains: ["ethereum"], needs_key: "base" })).toBe(true);
    expect(needsChains({ ...evm, hint: "ethereum" })).toBe(false);
    expect(needsChains({ ...evm, chain: "bitcoin", chains: [] })).toBe(false);
  });
});

describe("paths and links", () => {
  it("reads the chosen EVM chains, or all of them", () => {
    expect(lookupPath("0xabc", [])).toBe("lookup/0xabc");
    expect(lookupPath("0xabc", ["ethereum", "base"])).toBe("lookup/0xabc?chains=ethereum,base");
    expect(lookupPath("bitcoincash:qq1", [])).toBe("lookup/bitcoincash%3Aqq1");
  });

  it("opens the explorer without Bitcoin Cash's prefix", () => {
    expect(explorerHref("https://x.org/address/{}", "bitcoincash:qq1")).toBe("https://x.org/address/qq1");
    expect(explorerHref("https://etherscan.io/address/{}", "0xabc")).toBe("https://etherscan.io/address/0xabc");
  });
});

describe("moved", () => {
  const [hot, mixer] = LOOKUP.connections as Party[];
  it("says what went each way, how often and when", () => {
    expect(moved(hot!, t, "en")).toBe("out 0.5 ETH · 3 transfers · 2023-11-14 to 2024-03-09");
    expect(moved(mixer!, t, "en")).toBe("in 10 ETH · 1 transfer · 2023-11-14");
  });

  it("leaves out what is not known: a counterparty seen only through a guess", () => {
    expect(moved({ ...mixer!, count: 0, first: 0, last: 0, received: [] }, t, "en")).toBe("");
  });
});

describe("caveats", () => {
  it("lists every gap of a stranger's lookup, the live read last", () => {
    const list = caveats({ ...LOOKUP, errors: ["base: timeout"], truncated: true }, t);
    expect(list).toEqual([
      "Not everything could be read (base: timeout).",
      t("tracker-lookup:leaves.truncated"),
      "Transfers of the 2 tokens without a reliable price are left out of the counterparties: anyone can airdrop those.",
      "4 quieter counterparties not listed.",
      "Ethereum: only the 10000 newest transfers are read.",
      t("tracker-lookup:leaves.direct"),
      t("tracker-lookup:leaves.live"),
    ]);
  });

  it("drops what does not apply, and the direct-links note for one of mine", () => {
    expect(caveats({ ...LOOKUP, mine: 1, unvouched: 1, more: 0, notes: [] }, t)).toEqual([
      "Transfers of the 1 token without a reliable price are left out of the counterparties: anyone can airdrop those.",
      t("tracker-lookup:leaves.live"),
    ]);
  });
});
