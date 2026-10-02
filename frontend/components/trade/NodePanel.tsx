"use client";

/**
 * Inspector tab "Node" (phase-11 §11.2): the selected pipeline node's status,
 * duration and LLM usage, its transcript (reasoning + ToolCallTree of the calls
 * tagged with this node) and what it produced — report markdown and/or the
 * structured output (rendered + JSON).
 */

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { NoticeRow, ReasoningRow } from "@/components/conversation/ProcessRows";
import { ToolCallTree } from "@/components/conversation/ToolCallTree";
import { PolicyList } from "@/components/trade/DecisionCard";
import { JsonView } from "@/components/ui/JsonView";
import { StateDot, dotFor } from "@/components/ui/StateDot";
import { formatCost, formatDuration, formatTokens } from "@/lib/format";
import { isAnalystSubNode } from "@/lib/trade";
import type { TradeRunDetail } from "@/lib/trade-api";
import type { RunView } from "@/stores/run-reducer";

const SPEAKER: Record<string, string> = {
  bull_researcher: "bull",
  bear_researcher: "bear",
  aggressive: "aggressive",
  conservative: "conservative",
  neutral: "neutral",
};

/** What a node produced, from the run detail: markdown to render and/or a structured value. */
export function nodeOutput(node: string, detail: TradeRunDetail | undefined): { markdown?: string; data?: unknown } {
  if (!detail) return {};
  if (isAnalystSubNode(node)) {
    const rep = detail.reports?.[node.slice("analyst.".length)];
    return rep ? { markdown: rep.text || `_(${rep.status}${rep.error ? `: ${rep.error}` : ""})_`, data: rep } : {};
  }
  const speaker = SPEAKER[node];
  if (speaker) {
    const debate = node.endsWith("_researcher") ? detail.invest_debate : detail.risk_debate;
    const turns = (debate?.history ?? []).filter((t) => t.speaker === speaker);
    return turns.length ? { markdown: turns.map((t, i) => `**รอบ ${i + 1}**\n\n${t.text}`).join("\n\n---\n\n") } : {};
  }
  switch (node) {
    case "resolve_symbol":
      return { data: detail.instrument };
    case "load_context":
      return { data: detail.market_snapshot };
    case "analysts":
      return { data: detail.reports };
    case "research_manager":
      return detail.research_plan
        ? { markdown: String(detail.research_plan.thesis ?? ""), data: detail.research_plan }
        : {};
    case "trader":
      return { data: detail.trade_proposal };
    case "portfolio_manager":
      return detail.decision ? { markdown: detail.decision.summary, data: detail.decision } : {};
    case "policy_gate":
      return { data: detail.policy };
    case "approval":
      return { data: detail.approval };
    case "report":
      return detail.report_md ? { markdown: detail.report_md } : {};
    default:
      return {};
  }
}

export function NodePanel({
  node,
  view,
  detail,
}: {
  node: string | null;
  view: RunView | undefined;
  detail: TradeRunDetail | undefined;
}) {
  if (!node) return <div className="p-4 text-[12.5px] text-subtle">คลิก node ใน pipeline เพื่อดูรายละเอียด</div>;
  const run = view?.nodes[node];
  const label = run?.label ?? detail?.graph_view.nodes.find((n) => n.id === node)?.label ?? node;
  const llm = (view?.llm ?? []).filter((c) => c.node === node && !c.failed);
  const tokensIn = llm.reduce((s, c) => s + c.inputTokens, 0);
  const tokensOut = llm.reduce((s, c) => s + c.outputTokens, 0);
  const cost = llm.reduce((s, c) => s + c.costUsd, 0);
  const rootCalls = (view?.rootCallIds ?? []).filter((id) => view?.calls[id]?.node === node);
  const items = (view?.items ?? []).filter(
    (it) => (it.kind === "reasoning" || it.kind === "warning" || it.kind === "guard") && it.node === node,
  );
  const policy = node === "policy_gate" ? detail?.policy : null;
  const out = nodeOutput(node, detail);

  return (
    <div data-testid="node-panel" className="space-y-3 p-3 text-[12.5px]">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <StateDot state={dotFor(run?.status === "waiting" ? "partial" : (run?.status ?? "idle"))} label={run?.status ?? "idle"} />
          <h3 className="text-[14px] font-semibold">{label}</h3>
          <span className="num text-subtle">{node}</span>
        </div>
        <div className="num flex flex-wrap gap-x-3 text-[11.5px] text-muted">
          <span>{run?.status ?? "ยังไม่รัน"}</span>
          {run?.runs && run.runs > 1 ? <span>รัน {run.runs} ครั้ง</span> : null}
          <span>{formatDuration(run?.durationMs)}</span>
          {llm.length ? (
            <span>
              {llm.length} LLM · in {formatTokens(tokensIn)} · out {formatTokens(tokensOut)} · {formatCost(cost)}
            </span>
          ) : null}
          {run?.checkpointed ? <span>💾 checkpoint</span> : null}
        </div>
        {run?.summary ? <p className="text-muted">{run.summary}</p> : null}
      </header>

      {items.length || rootCalls.length ? (
        <section>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">Transcript</h4>
          <div className="space-y-0.5">
            {items.map((it) =>
              it.kind === "reasoning" ? (
                <ReasoningRow key={`r${it.seq}`} text={it.text} pending={it.pending} />
              ) : it.kind === "warning" || it.kind === "guard" ? (
                <NoticeRow key={`w${it.seq}`} item={it} />
              ) : null,
            )}
            {rootCalls.length && view ? <ToolCallTree callIds={rootCalls} calls={view.calls} runId={view.runId} /> : null}
          </div>
        </section>
      ) : null}

      {policy ? (
        <section>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">Policy</h4>
          <PolicyList results={policy.results} />
        </section>
      ) : null}

      {out.markdown ? (
        <section>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">Report</h4>
          <div data-testid="node-report" className="fh-markdown rounded-md border border-line bg-layer-1 p-2">
            <Markdown remarkPlugins={[remarkGfm]}>{out.markdown}</Markdown>
          </div>
        </section>
      ) : null}

      {out.data !== undefined && out.data !== null ? (
        <section>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">Structured output</h4>
          <JsonView data={out.data as Record<string, unknown>} />
        </section>
      ) : null}
    </div>
  );
}
