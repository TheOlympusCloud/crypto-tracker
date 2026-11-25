"use client";

import { Fragment, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";
import { api } from "@/lib/tracker/api";
import { fold } from "@/lib/tracker/fold";
import { day, num } from "@/lib/tracker/format";
import { chainLabel, chainOf, kindLabelKey, kindOf, title, typeOf } from "@/lib/tracker/kinds";
import type { Balance, GraphNode } from "@/lib/tracker/types";
import { useTracker } from "../TrackerProvider";
import { useLookup } from "../lookup/useLookup";
import { ChainMark, Coin, KindIcon } from "../Glyph";
import { BUTTON, Badge, PRIMARY, PrivacyScoreLink, TrustBadge, fake } from "../bits";
import { WalletGrade } from "../portfolio/Grade";
import { explorerUrl, namedBy } from "./text";

const CATEGORIES = ["cex", "dex", "bridge", "mixer", "flagged", "defi", "service", "other"];
const FIELD = "min-h-9 w-full rounded-lg border border-card-border bg-surface-inset px-3 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";

/** Any address of the graph on show: one of mine, a counterparty, or the looked-up address. */
type Node = Omit<GraphNode, "balances"> & { balances?: Balance[] };

/** Any address of the graph on show: what is known about it, and what can be done with it. */
export function NodePanel({ address }: { address: string }) {
  const { t, i18n } = useTranslation();
  const { graph, analysis, byAddress, scope, setScope, latest, collapsed, shownPath, minConf, act, fail, refresh, openWallet, openPanel, startSync, toggleFold } = useTracker();
  const { lookUp } = useLookup();
  const n: Node | undefined = scope ? [...scope.graph.nodes, scope, ...scope.connections].find((x) => x.address === address) : byAddress.get(address);
  if (!n) return null;

  // The wallet this address of mine belongs to, by its first address: where its score is kept.
  const wallet = n.mine ? analysis.verdicts.find((v) => v.wallet === (n.wallet || n.address))?.address : undefined;
  const folded = collapsed.has(n.address);
  // How many counterparties folding this address hides now.
  const hides = folded ? (fold(scope ? scope.graph : graph, collapsed, shownPath, minConf).nodes.find((x) => x.address === n.address)?.folded ?? 0) : 0;

  const rows: [string, ReactNode][] = [
    [t("tracker-graph:node.type"), typeOf(n, t)],
    [t("tracker-graph:node.named_by"), namedBy(n, t)],
    [t("tracker-graph:node.chain"), chainLabel(chainOf(n))],
    [t("tracker-graph:node.label"), n.label],
    [t("tracker-graph:node.synced"), n.synced_at ? day(n.synced_at) : t("tracker-graph:node.not_synced")],
    [t("tracker-graph:node.note"), n.hub ? t("tracker-graph:node.hub") : null],
    [t("tracker-graph:node.error"), n.error],
    ...(n.balances ?? []).map((b): [string, ReactNode] => [
      `${b.asset} · ${b.chain}`,
      fake(b) ? (
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{num(b.amount, i18n.language)}</span>
          <TrustBadge balance={b} />
        </span>
      ) : (
        <span className="font-mono">{num(b.amount, i18n.language)}</span>
      ),
    ]),
  ];

  const saveLabel = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const name = String(form.get("label") ?? "");
    const category = String(form.get("category") ?? "other");
    api("labels", { address: n.address, name, category })
      .then(() => {
        // The looked-up wallet is not part of the data a refresh brings: rename it here, if it is still shown.
        const v = latest().scope;
        if (v && v.address === scope?.address) {
          const rename = <T extends { address: string }>(x: T): T => (x.address === n.address ? { ...x, label: name, category: name ? category : undefined } : x);
          setScope({ ...rename(v), connections: v.connections.map(rename), graph: { ...v.graph, nodes: v.graph.nodes.map(rename) } });
        }
        return refresh();
      })
      .catch(fail);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 pr-8">
        {n.mine ? (
          <Coin public={n.public} size={48} />
        ) : (
          <span className="inline-flex size-12 shrink-0 items-center justify-center rounded-full bg-surface-inset">
            <KindIcon kind={kindOf(n)} size={32} />
          </span>
        )}
        <div className="min-w-0 space-y-1">
          <h2 className="break-words text-lg font-semibold text-foreground">{title(n)}</h2>
          {n.mine ? <Badge public={n.public} /> : null}
        </div>
      </div>
      <p className="flex items-start gap-2 break-all font-mono text-xs text-muted">
        <ChainMark chain={chainOf(n)} size={18} />
        {n.address}
      </p>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
        {rows.map(([term, value], i) =>
          value ? (
            <Fragment key={i}>
              <dt className="text-muted">{term}</dt>
              <dd className="break-words text-foreground">{value}</dd>
            </Fragment>
          ) : null,
        )}
      </dl>
      <div className="flex flex-wrap gap-2">
        {n.mine ? (
          <button type="button" className={PRIMARY} onClick={() => openWallet(n.address)}>
            {t("tracker-graph:node.verdict")}
          </button>
        ) : (
          <button type="button" className={BUTTON} onClick={act(() => api("wallets", { address: n.address, name: "", public: false }))}>
            {t("tracker-graph:node.mine")}
          </button>
        )}
        {scope ? (
          <button type="button" className={BUTTON} onClick={() => openPanel({ kind: "lookup" })}>
            {t("tracker-graph:node.back", { name: title(scope) })}
          </button>
        ) : (
          <button type="button" className={BUTTON} onClick={() => startSync([n.address])}>
            {n.synced_at ? t("tracker-graph:node.resync") : t("tracker-graph:node.expand")}
          </button>
        )}
        {n.mine || n.address === scope?.address ? null : (
          <button type="button" className={BUTTON} onClick={() => lookUp(n.address).catch(fail)}>
            {t("tracker-graph:node.lookup")}
          </button>
        )}
        {/* A busy address (an exchange, a mixer) can hide the counterparties that only deal with it. */}
        <button type="button" className={BUTTON} aria-pressed={folded} onClick={() => toggleFold(n.address)}>
          {!folded ? t("tracker-graph:node.fold") : hides ? t("tracker-graph:node.unfold_hidden", { count: hides }) : t("tracker-graph:node.unfold")}
        </button>
        {n.addresses ? null : <PrivacyScoreLink address={n.address} chain={chainOf(n)} />}
        {n.explorer_address ? (
          <a className={BUTTON} href={explorerUrl(n.explorer_address, n.address)} target="_blank" rel="noreferrer noopener">
            {t("tracker-graph:node.explorer")}
            <ExternalLink size={14} aria-hidden />
          </a>
        ) : null}
      </div>
      {wallet && (
        <section className="border-t border-card-border pt-4">
          <h3 className="mb-2 text-sm font-semibold text-foreground">{t("tracker-exposure:wallet.title")}</h3>
          <WalletGrade address={wallet} chain={chainOf(n)} />
        </section>
      )}
      {n.mine ? null : (
        <form key={n.address} onSubmit={saveLabel} className="space-y-3 border-t border-card-border pt-4">
          <label className="block space-y-1 text-sm text-muted">
            <span>{t("tracker-graph:form.label")}</span>
            <input name="label" defaultValue={n.label ?? ""} maxLength={80} placeholder={t("tracker-graph:form.placeholder")} className={FIELD} />
          </label>
          <label className="block space-y-1 text-sm text-muted">
            <span>{t("tracker-graph:form.type")}</span>
            <select name="category" defaultValue={n.category || "other"} className={FIELD}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {t(kindLabelKey(c))}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={PRIMARY}>
            {t("tracker-graph:form.save")}
          </button>
        </form>
      )}
    </div>
  );
}
