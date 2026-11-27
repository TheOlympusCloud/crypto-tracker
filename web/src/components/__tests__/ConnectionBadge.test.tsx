// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import React from "react";
import { render, screen, cleanup } from "@testing-library/react";

vi.mock("react-i18next", async () => {
  // crypto-tracker's strings from its bundled English namespace, the scanner's from defaultValue
  const ns = (await import("../../../public/locales/en/tracker-exposure.json")).default as Record<string, string>;
  const t = (key: string, opts: Record<string, unknown> = {}) =>
    key.startsWith("tracker-exposure:")
      ? (ns[key.slice("tracker-exposure:".length)] ?? key).replace(/\{\{(\w+)\}\}/g, (_, n: string) => String(opts[n]))
      : ((opts.defaultValue as string) ?? key);
  return { useTranslation: () => ({ t }) };
});

const network = vi.hoisted(() => ({
  value: {
    torStatus: "clearnet", localApiStatus: "unavailable", isUmbrel: false, customApiUrl: null as string | null,
    liteNode: null as { api: string; state: string } | null, config: { mempoolBaseUrl: "https://mempool.space/api" },
  },
}));
vi.mock("@/context/NetworkContext", () => ({ useNetwork: () => network.value }));

import { ConnectionBadge } from "../ConnectionBadge";

afterEach(cleanup);

describe("ConnectionBadge", () => {
  it("warns about clearnet on the default API", () => {
    network.value = { ...network.value, customApiUrl: null };
    render(<ConnectionBadge />);
    expect(screen.getByRole("button").getAttribute("aria-label")).toMatch(/mempool\.space can see your IP/);
  });

  it("does not claim clearnet to mempool.space with a custom API (Tor detection is skipped)", () => {
    network.value = { ...network.value, customApiUrl: "http://node.onion/api" };
    render(<ConnectionBadge />);
    const label = screen.getByRole("button").getAttribute("aria-label") ?? "";
    expect(label).not.toMatch(/can see your IP/);
    expect(label).toMatch(/custom API/);
  });

  it("with the lite node, says what goes to the node and that scans go to the public server", () => {
    network.value = { ...network.value, customApiUrl: null, liteNode: { api: "/api/tracker/node", state: "syncing" } };
    render(<ConnectionBadge />);
    const button = screen.getByRole("button");
    const label = button.getAttribute("aria-label") ?? "";
    expect(button.textContent).toBe("Lite node");
    expect(label).toMatch(/^Fees and broadcasts use your own Bitcoin node\. Address and transaction scans go to mempool\.space, which sees them\./);
    expect(label).toMatch(/still syncing/);
    expect(label).not.toMatch(/stay on your network/);
  });
});
