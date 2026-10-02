"use client";

/**
 * Trade page (ui-spec §5, phase-11 §11.3).
 * - no run id: symbol search + examples; once picked → price chart, setup bar, pipeline preview
 * - with run id: chart (+ decision price lines) · live pipeline graph · decision card ·
 *   approval takeover · report, following the run's events (replay from seq 0)
 */

import { Square } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useInspectorTabs } from "@/components/shell/InspectorRegistry";
import { DecisionCard } from "@/components/trade/DecisionCard";
import { EventRiskList } from "@/components/trade/EventRiskList";
import { TradeOutcomePanel } from "@/components/trade/TradeOutcomePanel";
import { PipelineGraph } from "@/components/trade/PipelineGraph";
import { PriceChart, type PriceLineSpec } from "@/components/trade/PriceChart";
import { ResumeBanner } from "@/components/trade/ResumeBanner";
import { RunSetupBar } from "@/components/trade/RunSetupBar";
import { AssetBadge, SymbolSearch, type SymbolOption } from "@/components/trade/SymbolSearch";
import { TradeApprovalPanel, type ApprovalOutcome } from "@/components/trade/TradeApprovalPanel";
import { TRADE_TABS } from "@/components/trade/TradeInspectorTabs";
import { StateDot, dotFor } from "@/components/ui/StateDot";
import { api, type SymbolInfo } from "@/lib/api";
import { formatCost, formatDuration } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { EXAMPLE_SYMBOLS, defaultAnalysts } from "@/lib/trade";
import {
  answeredApproval,
  orderLabel,
  tradeApi,
  type AnalystName,
  type ChartInterval,
  type TradeRunDetail,
  type TradeRunRequest,
} from "@/lib/trade-api";
import { isTerminal, type RunView } from "@/stores/run-reducer";
import { useRun } from "@/stores/run-store";
import { useTradeDetail, useTradeHistory, useTradeStore } from "@/stores/trade-store";
import { useUiStore } from "@/stores/ui-store";

function symbolOf(detail: TradeRunDetail | undefined): SymbolInfo | null {
  const inst = detail?.instrument;
  if (!inst?.valid || !inst.canonical) return null;
  return {
    raw: inst.raw,
    canonical: inst.canonical,
    asset_type: inst.asset_type ?? "stock",
    market: inst.market ?? "",
    currency: inst.currency ?? "",
    display: inst.display ?? inst.canonical,
  };
}

/** The chart timeframe a run was analysed on (backend `domain/models/horizon.py`). */
const HORIZON_INTERVAL: Record<string, ChartInterval | undefined> = { intraday: "15m", swing: "1h", position: "1d" };

/** Entry / stop / targets of the latest plan (approval > live re-price > PM) as chart price lines. */
export function decisionLines(detail: TradeRunDetail | undefined): PriceLineSpec[] {
  const approval = answeredApproval(detail);
  const approved = approval && approval.outcome !== "rejected" ? approval.sizing : undefined;
  const live = detail?.live?.refreshed ? detail.live.sizing : undefined;
  const sizing = approved ?? live ?? detail?.decision?.sizing;
  if (!sizing) return [];
  const lines: PriceLineSpec[] = [];
  if (typeof sizing.entry === "number") {
    const pending = orderLabel(sizing.order);
    lines.push({ price: sizing.entry, title: pending ?? "entry", kind: "entry", pending: pending !== null });
  }
  if (typeof sizing.stop === "number") lines.push({ price: sizing.stop, title: "stop", kind: "stop" });
  (sizing.targets ?? []).forEach((t, i) => lines.push({ price: t, title: `T${i + 1}`, kind: "target" }));
  return lines;
}

function useRunRecord(runId: string | null, view: RunView | undefined) {
  const [record, setRecord] = useState<Awaited<ReturnType<typeof api.getRun>> | null>(null);
  const status = view?.status;
  const approvals = view ? Object.values(view.approvals).map((a) => a.outcome).join(",") : "";
  const reload = useCallback(() => {
    if (!runId) return;
    api.getRun(runId).then(setRecord).catch(() => setRecord(null));
  }, [runId]);
  useEffect(() => {
    setRecord(null);
  }, [runId]);
  useEffect(reload, [reload, status, approvals]);
  return { record, reload };
}

function SymbolPicker({ onPick }: { onPick: (o: SymbolOption) => void }) {
  const pickExample = async (q: string) => {
    try {
      const r = await api.searchSymbols(q);
      const sym = r.normalized ?? r.matches[0]?.symbol;
      if (sym) onPick({ symbol: sym, name: sym.display });
    } catch {
      // backend down: the connection banner already says so
    }
  };
  return (
    <div data-testid="trade-empty" className="mx-auto flex max-w-xl flex-col items-center gap-3 py-16 text-center">
      <h2 className="text-[18px] font-semibold">วิเคราะห์เทรด 1 symbol ด้วยทีม AI</h2>
      <p className="text-[13px] text-muted">
        analysts → bull/bear debate → trader → risk debate → portfolio manager → policy → คุณอนุมัติ (ไม่มีการส่ง order จริง)
      </p>
      <SymbolSearch onPick={onPick} autoFocus />
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLE_SYMBOLS.map((s) => (
          <button
            key={s}
            type="button"
            data-testid="example-symbol"
            onClick={() => void pickExample(s)}
            className="rounded-full border border-line px-3 py-1 text-[12.5px] text-muted hover:border-accent hover:text-fg"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

export function TradeView({ runId }: { runId: string | null }) {
  const router = useRouter();
  const search = useSearchParams();
  useInspectorTabs(TRADE_TABS);
  const selectRun = useUiStore((s) => s.selectRun);
  const selectNode = useUiStore((s) => s.selectNode);
  const selectedNode = useUiStore((s) => s.selectedNode);
  const llmChoice = useUiStore((s) => s.llmChoice);
  const setLlmChoice = useUiStore((s) => s.setLlmChoice);
  const graph = useTradeStore((s) => s.graph);
  const loadGraph = useTradeStore((s) => s.loadGraph);
  const reloadHistory = useTradeHistory((s) => s.reload);

  const [picked, setPicked] = useState<SymbolInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const view = useRun(runId);
  const detail = useTradeDetail(runId);
  const { record, reload } = useRunRecord(runId, view);
  const live = view ? !isTerminal(view.status) : false;
  const now = useNow(live, 1000);

  useEffect(() => void loadGraph(), [loadGraph]);
  useEffect(() => {
    if (runId) selectRun(runId);
    selectNode(null);
  }, [runId, selectRun, selectNode]);

  // /trade?symbol=PTT (from "New run" or the command palette) preselects the symbol
  const querySymbol = search?.get("symbol") ?? null;
  useEffect(() => {
    if (runId || !querySymbol) return;
    api
      .searchSymbols(querySymbol)
      .then((r) => r.normalized && setPicked(r.normalized))
      .catch(() => {});
  }, [runId, querySymbol]);

  const symbol = runId ? symbolOf(detail) : picked;
  const runStatus = record?.run.status ?? view?.status ?? "pending";
  const stalled = !!view && !isTerminal(view.status) && ["failed", "interrupted"].includes(record?.run.status ?? "");
  const analysts: AnalystName[] = useMemo(() => {
    const fromInput = (view?.input.analysts ?? detail?.input.analysts) as AnalystName[] | null | undefined;
    return detail?.instrument?.analysts ?? (fromInput?.length ? fromInput : defaultAnalysts(symbol?.asset_type));
  }, [detail, view?.input.analysts, symbol?.asset_type]);
  const pending = view ? Object.values(view.approvals).find((a) => a.outcome === "pending") : undefined;
  const lines = useMemo(() => decisionLines(detail), [detail]);
  const graphView = graph ?? detail?.graph_view ?? null;

  const start = async (body: TradeRunRequest) => {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await tradeApi.start(body);
      void reloadHistory();
      router.push(`/trade/${run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const resolve = useCallback(
    async (answer: ApprovalOutcome) => {
      if (!pending) return;
      setBusy(true);
      try {
        await api.resolveApproval(pending.approvalId, answer);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [pending],
  );

  const resume = async () => {
    if (!runId) return;
    setBusy(true);
    try {
      await api.resumeRun(runId);
      setError(null);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (view && isTerminal(view.status)) void reloadHistory();
  }, [view?.status, view, reloadHistory]);

  if (!runId && !symbol) return <SymbolPicker onPick={(o) => setPicked(o.symbol)} />;

  const duration = view?.usage.durationMs ?? (view?.startedAt ? now - view.startedAt : null);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        {runId ? (
          <>
            <StateDot state={dotFor(pending ? "partial" : runStatus)} label={runStatus} />
            <h1 className="text-[15px] font-semibold">{symbol?.display ?? String(view?.input.symbol ?? "…")}</h1>
            {symbol ? <AssetBadge symbol={symbol} /> : null}
            <span className="text-[12px] text-muted">
              {pending ? "รออนุมัติ" : runStatus}
              {detail?.instrument?.analysis_date ? ` · backtest ${detail.instrument.analysis_date}` : ""}
            </span>
            <span className="num ml-auto text-[12px] text-subtle">
              {formatDuration(duration)} · {formatCost(view?.usage.cost ?? 0)} · {view?.usage.llmCalls ?? 0} LLM ·{" "}
              {view?.usage.toolCalls ?? 0} tools
            </span>
            {live && !pending && !stalled ? (
              <button
                type="button"
                onClick={() => void api.cancelRun(runId).catch(() => {})}
                className="inline-flex items-center gap-1 rounded-md bg-error/15 px-2 py-0.5 text-[12px] text-error"
              >
                <Square className="size-3" aria-hidden /> Stop
              </button>
            ) : null}
            {symbol && !live ? (
              <Link href={`/trade?symbol=${encodeURIComponent(symbol.canonical)}`} className="rounded-md bg-layer-2 px-2 py-0.5 text-[12px]">
                Run ใหม่
              </Link>
            ) : null}
          </>
        ) : (
          <>
            <SymbolSearch value={symbol} onPick={(o) => setPicked(o.symbol)} />
            <button type="button" onClick={() => setPicked(null)} className="text-[12px] text-subtle hover:text-fg">
              ล้าง
            </button>
          </>
        )}
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {error ? <p className="text-[12.5px] text-error">{error}</p> : null}
        {runId ? (
          <ResumeBanner
            status={record?.run.status ?? ""}
            resumable={record?.resumable ?? false}
            node={record?.current_node ?? null}
            busy={busy}
            onResume={() => void resume()}
          />
        ) : null}
        {!runId && symbol ? (
          <RunSetupBar
            key={symbol.canonical}
            symbol={symbol}
            busy={busy}
            llm={llmChoice}
            onLlmChange={setLlmChoice}
            onRun={(b) => void start(b)}
          />
        ) : null}
        {symbol ? (
          <PriceChart
            key={`chart:${runId ?? "new"}`}
            symbol={symbol.canonical}
            runId={runId}
            asOf={detail?.instrument?.analysis_date ?? null}
            lines={lines}
            initialInterval={runId ? HORIZON_INTERVAL[String(detail?.input.horizon ?? "")] : undefined}
          />
        ) : null}
        {graphView ? (
          <PipelineGraph
            graph={graphView}
            analysts={analysts}
            view={view}
            input={view?.input ?? {}}
            now={now}
            stalled={stalled}
            selected={selectedNode}
            onSelect={(n) => selectNode(n)}
          />
        ) : null}
        {runId && detail?.decision ? <DecisionCard detail={detail} runId={runId} /> : null}
        {runId && detail?.trade_result ? (
          <TradeOutcomePanel
            key={`outcome:${runId}`}
            result={detail.trade_result}
            onSettle={async (body) => {
              try {
                await tradeApi.settle(runId, body);
                await useTradeStore.getState().load(runId);
                return null;
              } catch (err) {
                return err instanceof Error ? err.message : String(err);
              }
            }}
          />
        ) : null}
        {runId && detail?.event_risk ? <EventRiskList risk={detail.event_risk} /> : null}
        {runId && detail?.outcome && !detail.decision ? (
          <p className="text-[13px] text-muted">
            ผล: <b>{detail.outcome.status}</b> {detail.outcome.reason ? `· ${detail.outcome.reason}` : ""}
          </p>
        ) : null}
        {detail?.report_md ? (
          <section data-testid="trade-report" className="fh-markdown rounded-md border border-line bg-layer-1 p-3 text-[13px]">
            <Markdown remarkPlugins={[remarkGfm]}>{detail.report_md}</Markdown>
          </section>
        ) : null}
      </div>

      {pending ? (
        <div className="border-t border-line px-4 py-2">
          <TradeApprovalPanel approval={pending} busy={busy} onResolve={(a) => void resolve(a)} />
        </div>
      ) : null}
    </div>
  );
}
