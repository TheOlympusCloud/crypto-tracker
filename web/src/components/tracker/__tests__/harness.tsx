import { vi } from "vitest";
import { render } from "@testing-library/react";
import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { TrackerContext, type Tracker } from "../TrackerProvider";
import type { Analysis, Graph, GraphNode } from "@/lib/tracker/types";
import graphFixture from "./fixtures/graph.json";
import analysisFixture from "./fixtures/analysis.json";

/**
 * Test harness for the wallet tracker's views. The fixtures are a demo database's API answers
 * (invented wallet names on public addresses). In a test file:
 *
 *   vi.mock("react-i18next", async () => (await import("./harness")).i18nMock());
 *   renderTracker(<PortfolioView />, { openWallet: vi.fn() });
 */
export const GRAPH = graphFixture as Graph;
// Through unknown: JSON gives the findings' params of different checks no common type.
export const ANALYSIS = analysisFixture as unknown as Analysis;

/** A tracker over the fixtures with every action a mock; `over` replaces any part. */
export function fakeTracker(over: Partial<Tracker> = {}): Tracker {
  const byAddress = new Map<string, GraphNode>(GRAPH.nodes.map((n) => [n.address, n]));
  const nameOf = (a: string) => byAddress.get(a)?.name || a;
  return {
    graph: GRAPH,
    analysis: ANALYSIS,
    byAddress,
    loaded: true,
    status: { text: "", level: "" },
    setStatus: vi.fn(),
    fail: vi.fn(),
    refresh: vi.fn(async () => {}),
    act: vi.fn((fn: () => Promise<unknown>) => () => void fn()),
    view: "portfolio",
    show: vi.fn(),
    panel: null,
    openPanel: vi.fn(),
    closePanel: vi.fn(),
    openWallet: vi.fn(),
    selectNode: vi.fn(),
    scope: null,
    setScope: vi.fn(),
    shownPaths: [],
    showPath: vi.fn(),
    showGroup: vi.fn(),
    collapsed: new Set(),
    toggleFold: vi.fn(),
    minConf: 0,
    setMinConf: vi.fn(),
    spam: false,
    setSpam: vi.fn(),
    focus: null,
    requestFocus: vi.fn(),
    sync: null,
    startSync: vi.fn(),
    nameOf,
    latest: () => ({ panel: null, scope: over.scope ?? null }),
    ...over,
  };
}

/** Provides `tracker`, with the panel following openPanel and closePanel as in the real page (calls are still recorded). */
export function TrackerHarness({ tracker, children }: { tracker: Tracker; children: ReactNode }) {
  const [panel, setPanel] = useState(tracker.panel);
  const now = useRef({ panel: tracker.panel, scope: tracker.scope });
  useEffect(() => {
    const put = (p: typeof panel) => {
      now.current.panel = p;
      setPanel(p);
    };
    vi.mocked(tracker.openPanel).mockImplementation(put);
    vi.mocked(tracker.setScope).mockImplementation((v) => void (now.current.scope = v));
    vi.mocked(tracker.closePanel).mockImplementation(() => {
      now.current.scope = null; // as the page does: leaving a lookup brings my graph back
      put(null);
    });
  }, [tracker]);
  const latest = () => ({ ...now.current });
  return <TrackerContext.Provider value={{ ...tracker, panel, latest }}>{children}</TrackerContext.Provider>;
}

export function renderTracker(ui: ReactElement, over: Partial<Tracker> = {}) {
  const tracker = fakeTracker(over);
  return { tracker, ...render(<TrackerHarness tracker={tracker}>{ui}</TrackerHarness>) };
}
