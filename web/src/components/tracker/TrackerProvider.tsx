"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/tracker/api";
import { short, title } from "@/lib/tracker/kinds";
import type { Analysis, Graph, GraphNode, LookupResult, SyncStatus } from "@/lib/tracker/types";
import { setHash } from "@/lib/hash-nav";
import { useLocationHash } from "@/components/chrome/useLocationHash";
import { createLocalStorageStore } from "@/hooks/createLocalStorageStore";

export type View = "portfolio" | "graph" | "manage";
const VIEWS: View[] = ["portfolio", "graph", "manage"];

/** What the side panel shows. Lookup panels read the looked-up address from `scope`. */
export type PanelState =
  | { kind: "wallet"; address: string }
  | { kind: "node"; address: string }
  | { kind: "path"; path: string[]; heading: string; back: string }
  | { kind: "group"; address: string }
  | { kind: "lookup" }
  | { kind: "lookup-busy"; address: string }
  | { kind: "lookup-error"; address: string; chains: string[]; message: string }
  | { kind: "chains"; address: string; chains: string[]; picked: string[]; missing: string };

export interface Status {
  text: string;
  level: "" | "busy" | "error";
}

export interface Tracker {
  graph: Graph;
  analysis: Analysis;
  /** Every address in the graph, plus HD wallets none of whose addresses is known yet. */
  byAddress: Map<string, GraphNode>;
  loaded: boolean;
  status: Status;
  setStatus: (s: Status) => void;
  /** Shows an error in the status line. */
  fail: (e: unknown) => void;
  refresh: () => Promise<void>;
  /** A click handler: runs `fn`, then refreshes; an error goes to the status line. */
  act: (fn: () => Promise<unknown>) => () => void;
  view: View;
  show: (v: View) => void;
  panel: PanelState | null;
  openPanel: (p: PanelState) => void;
  closePanel: () => void;
  /** One of my wallets: its verdict, origin and actions. */
  openWallet: (address: string) => void;
  /** Any address of the graph on show (mine, or the looked-up one's). */
  selectNode: (address: string) => void;
  /** The looked-up address whose connections the graph shows instead of mine; null for mine. */
  scope: LookupResult | null;
  setScope: (v: LookupResult | null) => void;
  /** The panel and lookup on show right now, for an answer that arrives after its caller is gone. */
  latest: () => { panel: PanelState | null; scope: LookupResult | null };
  /** The paths drawn as fat orange lines in the graph: one, or every link of a group. */
  shownPaths: string[][];
  /** Opens the graph with `path` highlighted and spelled out; an empty path focuses `back`. */
  showPath: (path: string[], heading: string, back: string) => void;
  /** Opens the graph with the group of wallets that holds `address` highlighted, and its panel. */
  showGroup: (address: string) => void;
  /** Addresses whose counterparties are folded into them in the graph, kept in this browser. */
  collapsed: ReadonlySet<string>;
  toggleFold: (address: string) => void;
  /** Guesses below this confidence are not drawn (0 to 1). */
  minConf: number;
  setMinConf: (v: number) => void;
  /** Also draw transfers of unknown tokens. */
  spam: boolean;
  setSpam: (v: boolean) => void;
  /** A request for the graph to frame this address; `n` changes with every request. */
  focus: { address: string; n: number } | null;
  requestFocus: (address: string) => void;
  sync: SyncStatus | null;
  /** Syncs these addresses or HD wallet ids (all my wallets when empty), then follows it. */
  startSync: (addresses: string[], depth?: number, labels?: boolean) => void;
  /** A wallet's or address's name for a sentence. */
  nameOf: (address: string) => string;
}

const EMPTY_GRAPH: Graph = { nodes: [], edges: [], links: [] };
const EMPTY_ANALYSIS: Analysis = {
  max_hops: 4,
  hop_decay: 0.7,
  chains: [],
  fiat: null,
  alerts: { unread: 0, items: [] },
  watched: [],
  verdicts: [],
  origins: {},
};
const EMPTY: readonly string[] = [];
const parseFolded = (raw: string) => {
  const saved = JSON.parse(raw) as unknown;
  return Array.isArray(saved) ? saved.filter((a): a is string => typeof a === "string") : EMPTY;
};
const foldedStore = createLocalStorageStore<readonly string[]>("tracker-folded", EMPTY, parseFolded);
const PHASES = ["labels", "swaps", "prices", "tokens", "waiting"];

const Ctx = createContext<Tracker | null>(null);
/** For tests that render a view with a made-up tracker. */
export const TrackerContext = Ctx;

export function useTracker(): Tracker {
  const t = useContext(Ctx);
  if (!t) throw new Error("useTracker outside TrackerProvider");
  return t;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function TrackerProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [graph, setGraph] = useState<Graph>(EMPTY_GRAPH);
  const [analysis, setAnalysis] = useState<Analysis>(EMPTY_ANALYSIS);
  const [loaded, setLoaded] = useState(false);
  const [note, setNote] = useState<Status | null>(null);
  const hash = useLocationHash().slice(1) as View;
  const view = VIEWS.includes(hash) ? hash : "portfolio";
  const [panel, setPanelState] = useState<PanelState | null>(null);
  const [scope, setScopeState] = useState<LookupResult | null>(null);
  // Written with the state, read later by callers that may have unmounted meanwhile.
  const now = useRef<{ panel: PanelState | null; scope: LookupResult | null }>({ panel: null, scope: null });
  const setPanel = useCallback((p: PanelState | null) => {
    now.current.panel = p;
    setPanelState(p);
  }, []);
  const setScope = useCallback((v: LookupResult | null) => {
    now.current.scope = v;
    setScopeState(v);
  }, []);
  const latest = useCallback(() => ({ ...now.current }), []);
  const [shownPaths, setShownPaths] = useState<string[][]>([]);
  const folded = useSyncExternalStore(foldedStore.subscribe, foldedStore.getSnapshot, foldedStore.getServerSnapshot);
  const [foldedHere, setFoldedHere] = useState<readonly string[] | null>(null); // this visit's folds, also where nothing is kept
  const collapsed = useMemo(() => new Set(foldedHere ?? folded), [foldedHere, folded]);
  const [minConf, setMinConf] = useState(0);
  const [spam, setSpam] = useState(false);
  const [focus, setFocus] = useState<{ address: string; n: number } | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [syncs, setSyncs] = useState(0); // bumped by every sync started here

  const fail = useCallback((e: unknown) => setNote({ text: message(e), level: "error" }), []);

  const byAddress = useMemo(() => {
    const m = new Map(graph.nodes.map((n) => [n.address, n]));
    // An HD wallet none of whose addresses was found has a verdict but no node in the graph.
    for (const v of analysis.verdicts)
      if (!m.has(v.address)) m.set(v.address, { address: v.address, wallet: v.wallet, name: v.name, public: v.public, mine: 1, addresses: [] });
    return m;
  }, [graph, analysis]);
  const nameOf = useCallback((a: string) => {
    const n = byAddress.get(a);
    return n ? title(n) : short(a);
  }, [byAddress]);

  const refresh = useCallback(async () => {
    // Asking for the portfolio first refreshes prices older than ten minutes; values are optional.
    await api("portfolio").catch(() => null);
    const [g, a] = await Promise.all([api<Graph>(`graph?spam=${spam}`), api<Analysis>("analysis")]);
    setGraph(g);
    setAnalysis(a);
    setLoaded(true);
  }, [spam]);
  const act = useCallback((fn: () => Promise<unknown>) => () => {
    fn().then(refresh).catch(fail);
  }, [refresh, fail]);

  // ---------- sync: followed until it ends, then the data is read again ----------
  const startSync = useCallback((addresses: string[], depth = 0, labels = false) => {
    api("sync", { addresses, depth, labels })
      .then(() => {
        setNote(null);
        setSyncs((n) => n + 1);
      })
      .catch(fail);
  }, [fail]);
  useEffect(() => {
    let stop = false;
    let filled = false; // the background fill-in was seen reading: read the data again once it is done
    const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));
    void (async () => {
      let failed = false;
      while (!stop) {
        let s: SyncStatus;
        try {
          s = await api<SyncStatus>("sync");
        } catch (e) {
          // The server did not answer: say so (not "Syncing" any more), and ask again.
          if (stop) return;
          failed = true;
          setSync(null);
          fail(e);
          await wait(5000);
          continue;
        }
        if (stop) return;
        if (failed) setNote(null); // it answers again: the error is over
        failed = false;
        setSync(s);
        if (s.running) await wait(1500);
        else if (s.filling) await wait(10000);
        else if (s.node) await wait(30000); // the node's progress: it changes every few seconds
        else {
          if (s.total || filled) await refresh();
          return;
        }
        filled = Boolean(s.filling);
      }
    })().catch(fail);
    return () => {
      stop = true;
    };
  }, [syncs, refresh, fail]);
  useEffect(() => {
    // Again when the unknown-tokens toggle changes what the graph holds.
    void (async () => refresh())().catch(fail);
  }, [refresh, fail]);

  // A running sync speaks in the status line; otherwise the last error or message, or the sync's notes.
  const phase = (current: string) => (PHASES.includes(current) ? t(`tracker:sync.phase.${current}`) : nameOf(current));
  const status: Status = sync?.running
    ? { text: t("tracker:sync.running", { done: sync.done, total: sync.total, what: phase(sync.current) }), level: "busy" }
    : sync?.filling
      ? { text: t("tracker:sync.filling", { name: nameOf(sync.filling) }), level: "busy" }
      : (note ?? { text: sync?.error || (sync?.notes ?? []).join(" · "), level: sync?.error ? "error" : "" });

  // ---------- views and the panel ----------
  const show = useCallback((v: View) => setHash(v === "portfolio" ? "" : v), []);
  const requestFocus = useCallback((address: string) => setFocus((f) => ({ address, n: (f?.n ?? 0) + 1 })), []);
  const closePanel = useCallback(() => {
    setScope(null); // leaving a lookup brings my graph back
    setPanel(null);
  }, [setScope, setPanel]);
  const openWallet = useCallback((address: string) => {
    setScope(null);
    setPanel({ kind: "wallet", address });
  }, [setScope, setPanel]);
  const selectNode = useCallback((address: string) => setPanel({ kind: "node", address }), [setPanel]);
  const lookAt = useCallback((v: LookupResult | null) => {
    setScope(v);
    if (v) setShownPaths([]); // a looked-up address shows its own connections, no path of mine
  }, [setScope]);
  const showPath = useCallback((path: string[], heading: string, back: string) => {
    setScope(null);
    setShownPaths(path.length ? [path] : []);
    show("graph");
    if (path.length) setPanel({ kind: "path", path, heading, back });
    else {
      requestFocus(back);
      setPanel({ kind: "node", address: back });
    }
  }, [show, requestFocus, setScope, setPanel]);
  const showGroup = useCallback((address: string) => {
    setScope(null);
    setShownPaths(analysis.groups?.find((g) => g.wallets.includes(address))?.links.map((k) => k.path) ?? []);
    show("graph");
    setPanel({ kind: "group", address });
  }, [analysis, show, setScope, setPanel]);
  const toggleFold = useCallback((address: string) => {
    const next = new Set(collapsed);
    if (!next.delete(address)) next.add(address);
    setFoldedHere([...next]);
    foldedStore.set([...next]); // where the browser keeps nothing, the folds last for this visit
    requestFocus(address); // frame what is left around it
  }, [collapsed, requestFocus]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePanel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [closePanel]);

  const value: Tracker = {
    graph, analysis, byAddress, loaded, status, setStatus: setNote, fail, refresh, act,
    view, show, panel, openPanel: setPanel, closePanel, openWallet, selectNode,
    scope, setScope: lookAt, latest, shownPaths, showPath, showGroup, collapsed, toggleFold,
    minConf, setMinConf, spam, setSpam, focus, requestFocus, sync, startSync, nameOf,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
