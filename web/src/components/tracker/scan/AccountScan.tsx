"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { ADDRESS_BASE_SCORE } from "@/lib/scoring/score";
import { buildResultViewModel } from "@/lib/view/tx-view-model";
import { gradeTagline } from "@/lib/view/verdict";
import { GradeDial } from "@/components/results/GradeDial";
import { FindingsList } from "@/components/results/FindingsList";
import { ScoreBreakdown } from "@/components/results/ScoreBreakdown";
import { useLocationHash } from "@/components/chrome/useLocationHash";
import { setHash } from "@/lib/hash-nav";
import { api } from "@/lib/tracker/api";
import { exposurePath, gapText, readScanHash, toResult } from "@/lib/tracker/exposure";
import { chainLabel, short } from "@/lib/tracker/kinds";
import type { Exposure, Resolved, TrackerFinding } from "@/lib/tracker/types";
import { explorerHref, needsChains } from "@/components/tracker/lookup/logic";
import { ChainMark } from "@/components/tracker/Glyph";
import { BUTTON, CARD, PRIMARY } from "@/components/tracker/bits";

type Step = { step: "chains"; found: Resolved } | { step: "error"; message: string } | { step: "done"; exposure: Exposure };

/**
 * The privacy scan of an EVM, Solana or Tron address (#addr=<address>&chains=<chain,…>): the server's
 * findings and score, shown with am-i-exposed's dial, findings list and breakdown. An EVM address
 * without chains first asks which ones to read, as a lookup does.
 */
export function AccountScan() {
  const { t } = useTranslation();
  const hash = useLocationHash();
  const [attempt, setAttempt] = useState(0);
  // What the scan of `key` came to; anything else is still on its way.
  const [state, setState] = useState<{ key: string } & Step>();
  const key = `${hash}|${attempt}`;
  const { address, chains } = readScanHash(hash);

  useEffect(() => {
    if (!address) return;
    let live = true;
    void (async () => {
      try {
        let picked = chains;
        if (!picked.length) {
          const found = await api<Resolved>("resolve", { q: address });
          if (needsChains(found)) {
            if (live) setState({ key, step: "chains", found });
            return;
          }
          picked = found.hint ? [found.hint] : [];
        }
        const exposure = await api<Exposure>(exposurePath(address, picked));
        if (live) setState({ key, step: "done", exposure });
      } catch (e) {
        if (live) setState({ key, step: "error", message: e instanceof Error ? e.message : String(e) });
      }
    })();
    return () => void (live = false);
    // `key` holds the hash, from which address and chains come
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const now = state?.key === key ? state : undefined;
  return (
    <div className="mx-auto w-full max-w-[1360px] px-4 sm:px-6 lg:px-8 pt-5 pb-16 space-y-10">
      <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground min-h-[44px]">
        <ArrowLeft size={15} aria-hidden="true" />
        {t("tracker-exposure:new")}
      </Link>
      {!address ? (
        <p className="text-muted">{t("tracker-exposure:empty")}</p>
      ) : !now ? (
        <p role="status" className="animate-pulse text-sm text-bitcoin motion-reduce:animate-none">
          {t("tracker-exposure:busy")}
        </p>
      ) : now.step === "chains" ? (
        <ChainChoice found={now.found} />
      ) : now.step === "error" ? (
        <section className={`${CARD} max-w-xl`}>
          <h1 className="text-xl font-semibold text-foreground">{t("tracker-exposure:error.title")}</h1>
          <p className="mt-2 break-all font-mono text-sm text-foreground">{address}</p>
          <p className="mt-3 text-sm text-danger">{now.message}</p>
          <button type="button" className={`mt-4 ${BUTTON}`} onClick={retry}>
            {t("tracker-lookup:error.retry")}
          </button>
        </section>
      ) : (
        <ScanResult key={key} exposure={now.exposure} onRetry={retry} />
      )}
    </div>
  );
}

function ChainChoice({ found }: { found: Resolved }) {
  const { t } = useTranslation();
  return (
    <section className={`${CARD} max-w-xl`}>
      <h1 className="text-xl font-semibold text-foreground">{t("tracker-lookup:chains.title")}</h1>
      <p className="mt-2 break-all font-mono text-sm text-foreground">{found.address}</p>
      <p className="mt-3 text-sm text-muted">{t("tracker-lookup:chains.hint")}</p>
      <form
        className="mt-3"
        onSubmit={(e) => {
          e.preventDefault();
          const picked = new FormData(e.currentTarget).getAll("chain").map(String);
          setHash(`addr=${encodeURIComponent(found.address)}&chains=${(picked.length ? picked : found.chains).join(",")}`);
        }}
      >
        <fieldset>
          <legend className="sr-only">{t("tracker-lookup:chains.title")}</legend>
          {found.chains.map((c) => (
            <label key={c} className="flex min-h-10 cursor-pointer items-center gap-2.5 rounded-lg px-2 text-sm text-foreground hover:bg-surface-inset">
              <input type="checkbox" name="chain" value={c} defaultChecked className="size-4 accent-bitcoin" />
              <ChainMark chain={c} size={18} />
              {chainLabel(c)}
            </label>
          ))}
        </fieldset>
        <button type="submit" className={`mt-3 ${PRIMARY}`}>
          {t("tracker-exposure:chains.submit")}
        </button>
      </form>
    </section>
  );
}

function ScanResult({ exposure, onRetry }: { exposure: Exposure; onRetry: () => void }) {
  const { t } = useTranslation();
  const result = toResult(exposure, t);
  const vm = buildResultViewModel({ result, baseScore: ADDRESS_BASE_SCORE });
  // Nothing found, or a partial read, is no clean bill: the dial shows the grade, the headline
  // stays neutral, as the wallet verdicts never say "safe".
  const neutral = exposure.findings.length === 0 ? "none" : exposure.partial ? "partial" : null;
  const tagline = gradeTagline(vm.grade, vm.all);
  const headline = neutral ? t(`tracker-exposure:headline.${neutral}`) : t(tagline.key, { defaultValue: tagline.defaultValue });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [openIds, setOpenIds] = useState<ReadonlySet<string>>(() => new Set(vm.groups.leaks.slice(0, 1).map((f) => f.id as string)));
  const toggle = useCallback((id: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const open = useCallback((id: string) => setOpenIds((prev) => new Set(prev).add(id)), []);

  return (
    <>
      <section aria-labelledby="verdict-title" className="panel grid gap-8 lg:gap-14 lg:grid-cols-[300px_minmax(0,1fr)] items-center">
        <div className="flex flex-col items-center" data-testid="score-display" data-grade={vm.grade} data-score={vm.score}>
          <GradeDial score={vm.score} size={300} />
        </div>
        <div className="min-w-0 space-y-4">
          <p className="eyebrow">{t("tracker-exposure:title")}</p>
          <a
            href={explorerHref(exposure.explorer_address, exposure.address)}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 break-all font-mono text-sm text-muted hover:text-foreground"
          >
            <ChainMark chain={exposure.icon} size={18} />
            {exposure.address}
            <ExternalLink size={13} className="shrink-0" aria-hidden="true" />
          </a>
          <h1 id="verdict-title" className="text-3xl sm:text-4xl font-semibold tracking-tight text-balance leading-tight">
            {headline}
          </h1>
          {exposure.chain === "evm" && <p className="text-sm text-muted">{t("tracker-exposure:chains.read", { chains: exposure.chains.map(chainLabel).join(", ") })}</p>}
          {exposure.partial && (
            <div className="rounded-lg border border-dashed border-card-border bg-surface-inset px-3 py-2.5 text-sm" data-testid="partly-checked">
              <b className="font-semibold text-foreground">{t("tracker-exposure:partly")}</b>
              <ul className="mt-1 list-disc pl-5 text-muted">
                {exposure.gaps.map((g, i) => (
                  <li key={i}>{gapText(g, t)}</li>
                ))}
              </ul>
              <button type="button" onClick={onRetry} className="mt-1 text-bitcoin hover:underline underline-offset-4">
                {t("tracker-lookup:error.retry")}
              </button>
            </div>
          )}
          <p className="max-w-[70ch] text-sm text-muted">{t("tracker-exposure:checked")}</p>
        </div>
      </section>

      <div className="grid gap-10 xl:grid-cols-[minmax(0,8fr)_minmax(0,4fr)] items-start">
        <div className="min-w-0 space-y-10">
          <FindingsList visible={vm.visible} openIds={openIds} onToggle={toggle} highlightId={hoverId} onHover={setHoverId} />
          <EvidenceList findings={result.findings} />
        </div>
        <aside className="min-w-0 space-y-10 xl:sticky xl:top-[calc(var(--header-h,4rem)+1rem)]">
          <ScoreBreakdown
            waterfall={vm.waterfall}
            findings={vm.all}
            isRevealed={() => true}
            highlightId={hoverId}
            onHover={setHoverId}
            onOpen={open}
            zeroImpactCount={vm.visible.filter((f) => f.scoreImpact === 0).length}
          />
        </aside>
      </div>
    </>
  );
}

/** The transactions each finding rests on, at the chain's explorer: upstream's finding cards have no place for them. */
function EvidenceList({ findings }: { findings: TrackerFinding[] }) {
  const { t } = useTranslation();
  const shown = findings.filter((f) => f.evidence.length);
  if (!shown.length) return null;
  return (
    <section aria-labelledby="evidence-title">
      <h2 id="evidence-title" className="eyebrow mb-3">
        {t("tracker-exposure:evidence.title")}
      </h2>
      <ul className="space-y-3">
        {shown.map((f) => (
          <li key={f.id}>
            <p className="text-sm text-foreground">{f.title}</p>
            <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {f.evidence.map((e) => (
                <li key={`${e.chain}:${e.txid}`}>
                  <a href={e.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 font-mono text-sm text-muted hover:text-foreground">
                    <ChainMark chain={e.chain} size={14} />
                    {short(e.txid)}
                    <ExternalLink size={12} aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}
