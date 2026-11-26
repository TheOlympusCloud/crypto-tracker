// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { configure, act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// The renderers load through a dynamic import: give it more than a second when every test runs at once.
configure({ asyncUtilTimeout: 5000 });

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());
// jsdom has no WebGL and no canvas: the renderers are stand-ins that record their calls.
vi.mock("../graph3d", () => ({ create3d: vi.fn() }));
vi.mock("../graph2d", () => ({ create2d: vi.fn() }));

import { DARK_PALETTE } from "@/lib/palette";
import type { LookupResult } from "@/lib/tracker/types";
import type { Tracker } from "../../TrackerProvider";
import { GRAPH, fakeTracker, renderTracker } from "../../__tests__/harness";
import { t } from "../../__tests__/i18n-mock";
import { TrackerContext } from "../../TrackerProvider";
import type { Renderer, RendererOptions } from "../draw";
import { create2d } from "../graph2d";
import { create3d } from "../graph3d";
import { GraphView } from "../GraphView";

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const STRANGER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa";
const PUBLIC = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh";

const stub = () => ({
  setLook: vi.fn<Renderer["setLook"]>(),
  setData: vi.fn<Renderer["setData"]>(),
  highlight: vi.fn<Renderer["highlight"]>(),
  focus: vi.fn<Renderer["focus"]>(),
  setActive: vi.fn<Renderer["setActive"]>(),
  destroy: vi.fn<Renderer["destroy"]>(),
});
let r3 = stub();
let r2 = stub();

function screenIs(narrow: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({ matches: narrow && query === "(max-width: 760px)", media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
}

beforeEach(() => {
  r3 = stub();
  r2 = stub();
  vi.mocked(create3d).mockImplementation(() => r3);
  vi.mocked(create2d).mockImplementation(() => r2);
  screenIs(false);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const toggle = () => screen.getByRole("button", { name: "3D graph" });
const legend = () => screen.getByText("Legend").closest("details")!;

describe("GraphView", () => {
  it("draws the graph in 3D on a wide screen", async () => {
    renderTracker(<GraphView active />);
    await waitFor(() => expect(r3.setData).toHaveBeenCalled());
    expect(create2d).not.toHaveBeenCalled();
    expect(r3.setLook).toHaveBeenCalledWith({ palette: DARK_PALETTE, t });
    expect(r3.setData).toHaveBeenCalledWith(GRAPH, { minConf: 0 });
    expect(r3.setActive).toHaveBeenLastCalledWith(true);
    expect(r3.highlight).toHaveBeenLastCalledWith([]);
    expect(toggle().getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("img", { name: /^Graph of wallets and transfers/ })).toBeTruthy();
  });

  it("draws the flat graph on a narrow screen", async () => {
    screenIs(true);
    renderTracker(<GraphView active />);
    await waitFor(() => expect(r2.setData).toHaveBeenCalledWith(GRAPH, { minConf: 0 }));
    expect(create3d).not.toHaveBeenCalled();
    expect(toggle().getAttribute("aria-pressed")).toBe("false");
  });

  it("falls back to the flat graph without WebGL", async () => {
    vi.mocked(create3d).mockImplementation(() => {
      throw new Error("no WebGL");
    });
    renderTracker(<GraphView active />);
    await waitFor(() => expect(r2.setData).toHaveBeenCalled());
    expect(toggle().getAttribute("aria-pressed")).toBe("false");
    expect((toggle() as HTMLButtonElement).disabled).toBe(true);
  });

  it("switches between 3D and 2D, keeping only one running", async () => {
    renderTracker(<GraphView active />);
    await waitFor(() => expect(r3.setData).toHaveBeenCalled());
    fireEvent.click(toggle());
    await waitFor(() => expect(r2.setData).toHaveBeenCalled());
    expect(r3.setActive).toHaveBeenLastCalledWith(false);
    expect(r2.setActive).toHaveBeenLastCalledWith(true);
    fireEvent.click(toggle());
    expect(r3.setActive).toHaveBeenLastCalledWith(true);
    expect(r2.setActive).toHaveBeenLastCalledWith(false);
    expect(create3d).toHaveBeenCalledTimes(1); // the 3D scene was kept
  });

  it("makes and runs a renderer only while the view is shown", async () => {
    const tracker = fakeTracker();
    const view = (active: boolean) => (
      <TrackerContext.Provider value={tracker}>
        <GraphView active={active} />
      </TrackerContext.Provider>
    );
    const { rerender, unmount } = render(view(false));
    await act(async () => {});
    expect(create3d).not.toHaveBeenCalled();
    rerender(view(true));
    await waitFor(() => expect(r3.setActive).toHaveBeenLastCalledWith(true));
    rerender(view(false));
    expect(r3.setActive).toHaveBeenLastCalledWith(false);
    unmount();
    expect(r3.destroy).toHaveBeenCalled();
  });

  it("folds what is collapsed but keeps the shown path, and highlights it", async () => {
    renderTracker(<GraphView active />, { collapsed: new Set([COLD]), shownPaths: [[COLD, PUBLIC]] });
    await waitFor(() => expect(r3.setData).toHaveBeenCalled());
    const drawn = r3.setData.mock.calls[0]![0];
    expect(drawn.nodes.map((n) => n.address)).not.toContain(STRANGER);
    expect(drawn.nodes.find((n) => n.address === COLD)?.folded).toBe(1);
    expect(r3.highlight).toHaveBeenLastCalledWith([[COLD, PUBLIC]]);
  });

  it("draws a lookup's graph instead of mine", async () => {
    const graph = { nodes: [GRAPH.nodes[0]!], edges: [], links: [] };
    renderTracker(<GraphView active />, { scope: { address: COLD, graph } as unknown as LookupResult });
    await waitFor(() => expect(r3.setData).toHaveBeenCalledWith(graph, { minConf: 0 }));
  });

  it("frames each focus request once", async () => {
    const tracker = fakeTracker({ focus: { address: COLD, n: 1 } });
    const view = (over: Partial<Tracker>) => (
      <TrackerContext.Provider value={{ ...tracker, ...over }}>
        <GraphView active />
      </TrackerContext.Provider>
    );
    const { rerender } = render(view({}));
    await waitFor(() => expect(r3.focus).toHaveBeenCalledWith(COLD));
    rerender(view({ minConf: 0.5 }));
    expect(r3.focus).toHaveBeenCalledTimes(1);
    expect(r3.setData).toHaveBeenLastCalledWith(GRAPH, { minConf: 0.5 });
    rerender(view({ focus: { address: PUBLIC, n: 2 } }));
    expect(r3.focus).toHaveBeenLastCalledWith(PUBLIC);
  });

  it("opens a tapped node's panel; a tap beside the graph closes it, or goes back to the lookup", async () => {
    const { tracker } = renderTracker(<GraphView active />);
    await waitFor(() => expect(create3d).toHaveBeenCalled());
    const { onSelect } = vi.mocked(create3d).mock.calls[0]![1] as RendererOptions;
    onSelect(GRAPH.nodes[1]!);
    expect(tracker.selectNode).toHaveBeenCalledWith(PUBLIC);
    onSelect(null);
    expect(tracker.closePanel).toHaveBeenCalled();
    cleanup();

    const { tracker: looking } = renderTracker(<GraphView active />, { scope: { address: COLD, graph: GRAPH } as unknown as LookupResult });
    await waitFor(() => expect(create3d).toHaveBeenCalledTimes(2));
    (vi.mocked(create3d).mock.calls[1]![1] as RendererOptions).onSelect(null);
    expect(looking.openPanel).toHaveBeenCalledWith({ kind: "lookup" });
    expect(looking.closePanel).not.toHaveBeenCalled();
  });

  it("hides weak guesses and shows unknown tokens from its controls", () => {
    const { tracker } = renderTracker(<GraphView active={false} />, { minConf: 0.35 });
    expect(screen.getByText("35%")).toBeTruthy();
    const slider = screen.getByRole("slider");
    expect((slider as HTMLInputElement).value).toBe("35");
    fireEvent.change(slider, { target: { value: "50" } });
    expect(tracker.setMinConf).toHaveBeenCalledWith(0.5);
    fireEvent.click(screen.getByRole("checkbox", { name: "Unknown tokens" }));
    expect(tracker.setSpam).toHaveBeenCalledWith(true);
  });

  it("has a legend, open on wide screens and closed on narrow ones", () => {
    renderTracker(<GraphView active={false} />);
    expect(legend().open).toBe(true);
    for (const text of ["Private: sealed coin, padlock", "Public: open coin, eye", "Selected path", "Transfers", "Guess: common-input", "Guess: swap", "Exchange", "Mixer", "Unknown address"])
      expect(screen.getByText(text)).toBeTruthy();
    cleanup();
    screenIs(true);
    renderTracker(<GraphView active={false} />);
    expect(legend().open).toBe(false);
  });

  it("gives the legend's room to the side panel on a narrow graph, and takes it back", () => {
    // The graph measures 0 px wide here: narrower than the 900 px the legend needs beside the panel.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private readonly changed: () => void) {}
        observe() {
          this.changed();
        }
        disconnect() {}
      },
    );
    const tracker = fakeTracker();
    const view = (over: Partial<Tracker>) => (
      <TrackerContext.Provider value={{ ...tracker, ...over }}>
        <GraphView active={false} />
      </TrackerContext.Provider>
    );
    const panel = { kind: "node", address: COLD } as const;
    const { rerender } = render(view({}));
    expect(legend().open).toBe(true);
    rerender(view({ panel }));
    expect(legend().open).toBe(false);
    rerender(view({ panel: null }));
    expect(legend().open).toBe(true);
  });
});
