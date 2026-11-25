// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("./i18n-mock")).i18nMock());
vi.mock("@/lib/tracker/api", () => ({ api: vi.fn() }));

import { api } from "@/lib/tracker/api";
import type { LookupResult, SyncStatus } from "@/lib/tracker/types";
import { TrackerProvider, useTracker } from "../TrackerProvider";
import { useLookup } from "../lookup/useLookup";
import { ANALYSIS, GRAPH } from "./harness";

const IDLE: SyncStatus = { running: false, done: 0, total: 0, current: "", error: "", notes: [], filling: "" };
const FOUND = { address: "addr", icon: "bitcoin", graph: { nodes: [], edges: [], links: [] } } as unknown as LookupResult;

/** The server: answers for the page's data, and `sync` / `lookup` as each test says. */
function serve(sync: () => Promise<SyncStatus>, lookup: () => Promise<LookupResult> = async () => FOUND) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "sync") return sync();
    if (path.startsWith("lookup/")) return lookup();
    if (path.startsWith("graph")) return GRAPH;
    if (path === "analysis") return ANALYSIS;
    return {};
  });
}

function Probe() {
  const t = useTracker();
  const { openLookup } = useLookup();
  return (
    <div>
      <p data-testid="status">{`${t.status.level}|${t.status.text}`}</p>
      <p data-testid="panel">{t.panel?.kind ?? "none"}</p>
      <p data-testid="view">{t.view}</p>
      <p data-testid="folded">{[...t.collapsed].join(",")}</p>
      <button type="button" onClick={() => t.toggleFold("cex")}>fold</button>
      <button type="button" onClick={() => void openLookup("addr")}>look</button>
    </div>
  );
}
const text = (id: string) => screen.getByTestId(id).textContent;
const flush = () => act(async () => {});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("TrackerProvider", () => {
  it("says so when the sync status cannot be read during a sync, and keeps asking", async () => {
    vi.useFakeTimers();
    const answers: (() => Promise<SyncStatus>)[] = [
      async () => ({ ...IDLE, running: true, done: 1, total: 3 }),
      async () => {
        throw new Error("server down");
      },
      async () => IDLE,
    ];
    serve(() => (answers.shift() ?? (async () => IDLE))());
    render(<TrackerProvider><Probe /></TrackerProvider>);
    await flush();
    expect(text("status")).toMatch(/^busy\|Syncing 1\/3/);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(text("status")).toBe("error|server down"); // not "Syncing" any more
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(text("status")).toBe("|"); // it answers again: the error is gone
  });

  it("folds for this visit even where the browser keeps nothing", async () => {
    serve(async () => IDLE);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    render(<TrackerProvider><Probe /></TrackerProvider>);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "fold" }));
    expect(text("folded")).toBe("cex");
  });

  it("drops a lookup whose panel was closed before it answered", async () => {
    let answer: (v: LookupResult) => void = () => {};
    serve(async () => IDLE, () => new Promise((done) => (answer = done)));
    render(<TrackerProvider><Probe /></TrackerProvider>);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "look" }));
    expect(text("panel")).toBe("lookup-busy");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(text("panel")).toBe("none");
    await act(async () => answer(FOUND));
    expect(text("panel")).toBe("none");
    expect(text("view")).toBe("portfolio");
  });
});
