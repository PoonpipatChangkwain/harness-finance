import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { DecisionCard } from "@/components/trade/DecisionCard";
import { PipelineGraph } from "@/components/trade/PipelineGraph";
import { ResumeBanner } from "@/components/trade/ResumeBanner";
import { TradeApprovalPanel } from "@/components/trade/TradeApprovalPanel";
import type { ProvenanceRecord } from "@/lib/api";
import type { TradeRunDetail } from "@/lib/trade-api";
import { replay } from "@/stores/run-reducer";
import { loadTradingDetail, loadTradingGraph, loadTradingRun } from "@/tests/fixtures";

// React Flow measures nodes: jsdom has neither ResizeObserver nor DOMMatrixReadOnly
beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver ??= RO as unknown as typeof ResizeObserver;
  (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly ??= class {
    m22 = 1;
    constructor() {}
  };
});

const graph = loadTradingGraph();

describe("PipelineGraph", () => {
  it("renders one node per selected analyst and no fundamentals for forex", () => {
    render(<PipelineGraph graph={graph} analysts={["market", "news", "macro"]} />);
    const labels = screen.getAllByTestId("pipeline-node").map((n) => n.textContent ?? "");
    expect(labels.some((l) => l.includes("Macro"))).toBe(true);
    expect(labels.some((l) => l.includes("Fundamentals"))).toBe(false);
    expect(labels.some((l) => l.includes("Portfolio manager"))).toBe(true);
    expect(screen.getAllByTestId("pipeline-node").every((n) => n.dataset.status === "idle")).toBe(true);
  });

  it("follows the run's events: running analysts, waiting approval, rounds, checkpoints", () => {
    const events = loadTradingRun("ptt");
    const runId = events[0]!.run_id;
    const input = { debate_rounds: 2 };
    const analysts = ["market", "news", "fundamentals", "sentiment"] as const;
    const firstAnalystDone = events.find((e) => e.type === "node.finished" && e.payload.node.startsWith("analyst."))!;
    const mid = replay(runId, events.filter((e) => e.seq < firstAnalystDone.seq));
    const { rerender } = render(<PipelineGraph graph={graph} analysts={[...analysts]} view={mid} input={input} />);
    const status = () =>
      Object.fromEntries(screen.getAllByTestId("pipeline-node").map((n) => [n.textContent?.split(/\d|–|…/)[0]?.trim(), n.dataset.status]));
    expect(screen.getAllByTestId("pipeline-node").filter((n) => n.dataset.status === "running")).toHaveLength(4); // the 4 analysts blink together
    expect(status().Market).toBe("running");
    expect(status().Trader).toBe("idle");

    const required = events.find((e) => e.type === "approval.required")!;
    rerender(<PipelineGraph graph={graph} analysts={[...analysts]} view={replay(runId, events.filter((e) => e.seq <= required.seq))} input={input} />);
    const approval = screen.getAllByTestId("pipeline-node").find((n) => n.textContent?.startsWith("Approval"))!;
    expect(approval.dataset.status).toBe("waiting");
    expect(approval.className).toContain("fh-blink-warning");
    const bull = screen.getAllByTestId("pipeline-node").find((n) => n.textContent?.startsWith("Bull"))!;
    expect(within(bull).getByTestId("debate-round").textContent).toBe("รอบ 2/2");
    expect(screen.getAllByTestId("checkpoint-icon").length).toBeGreaterThan(5);

    rerender(<PipelineGraph graph={graph} analysts={[...analysts]} view={replay(runId, events)} input={input} />);
    expect(screen.getAllByTestId("pipeline-node").every((n) => n.dataset.status !== "running")).toBe(true);
  });

  it("shows running nodes as failed when the run stalled (server crash)", () => {
    const events = loadTradingRun("ptt");
    const trader = events.find((e) => e.type === "node.started" && e.payload.node === "trader")!;
    const view = replay(events[0]!.run_id, events.filter((e) => e.seq <= trader.seq));
    render(<PipelineGraph graph={graph} analysts={["market", "news", "fundamentals", "sentiment"]} view={view} stalled />);
    const node = screen.getAllByTestId("pipeline-node").find((n) => n.textContent?.startsWith("Trader"))!;
    expect(node.dataset.status).toBe("error");
  });
});

function records(detail: TradeRunDetail): Record<string, ProvenanceRecord> {
  const calcs = [
    "compute.risk.stop_from_atr@v1",
    "compute.risk.risk_reward@v1",
    "compute.risk.risk_reward@v1",
    "compute.risk.position_size@v1",
    "compute.trade.size@v1",
    "compute.trade.time_stop@v1",
  ];
  const out: Record<string, ProvenanceRecord> = {};
  const ids = detail.approval && "provenance_ids" in detail.approval ? (detail.approval.provenance_ids as string[]) : [];
  ids.forEach((id, i) => {
    out[id] = { id, calculation: calcs[i] ?? null, kind: "compute", value: null, source: "compute" } as ProvenanceRecord;
  });
  return out;
}

describe("DecisionCard", () => {
  it("cites every computed number and shows the edited sizing", async () => {
    const detail = loadTradingDetail("ptt");
    const recs = records(detail);
    const load = vi.fn(async (pid: string) => recs[pid] ?? null);
    render(<DecisionCard detail={detail} runId="run_x" load={load} />);
    expect(screen.getByTestId("rating-badge").textContent).toBe("Buy");
    expect(screen.getByTestId("verdict-badge").dataset.verdict).toBe("APPROVE");
    expect(screen.getByTestId("edited-note").textContent).toContain("50.00%");
    expect(screen.getByTestId("decision-card").textContent).toContain("4,100 units");
    // entry (snapshot) + stop + 2 × R:R + units + weight + time stop
    await waitFor(() => expect(screen.getAllByTestId("citation-chip")).toHaveLength(7));
    expect(load).toHaveBeenCalledTimes(6);
    expect(screen.getByTestId("time-stop").textContent).toContain("48 × 1h");
  });

  it("marks failed policy rules", () => {
    const detail = loadTradingDetail("eurusd");
    const results = detail.policy!.results.map((r) =>
      r.rule_id === "require_stop_loss" ? { ...r, passed: false, message: "no stop-loss" } : r,
    );
    // the gate's evaluation is shown when there is no newer one (live re-price / approval)
    const gateOnly = { ...detail, live: null, approval: null, policy: { blocked: true, results } };
    render(<DecisionCard detail={gateOnly} runId="run_x" load={async () => null} />);
    const failed = screen.getAllByTestId("policy-result").find((li) => li.dataset.passed === "false")!;
    expect(failed.textContent).toContain("✗");
    expect(failed.textContent).toContain("no stop-loss");
    expect(screen.getByTestId("decision-card").textContent).toContain("blocked");
  });

  it("shows a live re-priced entry with the snapshot close beside it", () => {
    const detail = loadTradingDetail("eurusd");
    const sizing = { ...detail.decision!.sizing, entry: 1.0812 };
    const live = {
      refreshed: true,
      quote: { price: 1.0812, ts: "2026-09-28T03:15:00Z", provider: "yfinance", provenance_ids: [] },
      snapshot_close: detail.market_snapshot?.close,
      sizing,
      provenance_ids: [],
      policy: detail.policy!,
    };
    render(<DecisionCard detail={{ ...detail, live, approval: null }} runId="run_x" load={async () => null} />);
    expect(screen.getByTestId("decision-card").textContent).toContain("1.0812");
    expect(screen.getByTestId("entry-live").textContent).toContain("snapshot");
  });

  it("shows a pending sell limit with the price it waits from and a time stop counted from the fill", () => {
    const detail = loadTradingDetail("eurusd");
    const sizing = {
      ...detail.decision!.sizing,
      entry: 1.0845,
      order_type: "limit" as const,
      order: "sell_limit" as const,
      market_price: 1.08,
      order_distance_atr: 0.75,
      order_error: null,
      order_expires_at: "2026-09-28T12:00:00Z",
      time_stop_from: "fill" as const,
      time_stop_at: null,
    };
    const shown = { ...detail, live: null, approval: null, decision: { ...detail.decision!, sizing } };
    render(<DecisionCard detail={shown} runId="run_x" load={async () => null} />);
    const badge = screen.getByTestId("order-badge");
    expect(badge.textContent).toBe("SELL LIMIT");
    expect(badge.dataset.valid).toBe("true");
    expect(screen.getByTestId("order-detail").textContent).toContain("0.75 ATR");
    expect(screen.getByTestId("decision-card").textContent).toContain("Pending entry");
    expect(screen.getByTestId("time-stop").textContent).toContain("นับจากตอนได้เข้า");
  });
});

describe("TradeApprovalPanel", () => {
  const approval = {
    approvalId: "apr_1",
    kind: "trade_decision",
    title: "PTT: Buy · long",
    proposal: { entry: 34.5, stop: 33.3, targets: [37, 39], units: 5700, position_weight: 0.19665 },
    policyResults: [{ rule_id: "require_stop_loss", passed: true, severity: "block", message: "stop-loss present" }],
    provenanceIds: [],
    outcome: "pending" as const,
  };

  it("sends an edited basis as the approval body", async () => {
    const onResolve = vi.fn();
    render(<TradeApprovalPanel approval={approval} onResolve={onResolve} />);
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("size percent"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("stop level"), { target: { value: "33" } });
    fireEvent.change(screen.getByLabelText("target levels"), { target: { value: "38, 40" } });
    fireEvent.submit(screen.getByTestId("approval-edit-form"));
    await waitFor(() => expect(onResolve).toHaveBeenCalledOnce());
    expect(onResolve).toHaveBeenCalledWith({
      outcome: "edited",
      edits: { final_size_pct: 0.5, stop_level: 33, target_levels: [38, 40] },
    });
  });

  it("does not send an invalid edit, and Enter / Esc approve / reject", async () => {
    const onResolve = vi.fn();
    render(<TradeApprovalPanel approval={approval} onResolve={onResolve} />);
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onResolve).toHaveBeenLastCalledWith({ outcome: "approved" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onResolve).toHaveBeenLastCalledWith({ outcome: "rejected" });
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("size percent"), { target: { value: "150" } });
    fireEvent.submit(screen.getByTestId("approval-edit-form"));
    await screen.findByText("0–100");
    expect(onResolve).toHaveBeenCalledTimes(2);
  });

  it("offers the order price only for a pending order", async () => {
    const { unmount } = render(<TradeApprovalPanel approval={approval} onResolve={vi.fn()} />);
    fireEvent.click(screen.getByText("Edit"));
    expect(screen.queryByLabelText("order price")).toBeNull();
    unmount();

    const onResolve = vi.fn();
    const pending = { ...approval, proposal: { ...approval.proposal, entry: 34.0, order: "buy_limit", market_price: 34.5 } };
    render(<TradeApprovalPanel approval={pending} onResolve={onResolve} />);
    expect(screen.getByTestId("approval-entry").textContent).toContain("BUY LIMIT");
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("order price"), { target: { value: "34.2" } });
    fireEvent.submit(screen.getByTestId("approval-edit-form"));
    await waitFor(() => expect(onResolve).toHaveBeenCalledWith({ outcome: "edited", edits: { order_price: 34.2 } }));
  });
});

describe("ResumeBanner", () => {
  it("shows for a resumable failed/interrupted run and resumes", () => {
    const onResume = vi.fn();
    const { rerender } = render(<ResumeBanner status="interrupted" resumable node="neutral" onResume={onResume} />);
    expect(screen.getByTestId("resume-banner").textContent).toContain("neutral");
    fireEvent.click(screen.getByTestId("resume-button"));
    expect(onResume).toHaveBeenCalledOnce();
    rerender(<ResumeBanner status="interrupted" resumable={false} node="neutral" onResume={onResume} />);
    expect(screen.queryByTestId("resume-banner")).toBeNull();
    rerender(<ResumeBanner status="completed" resumable node={null} onResume={onResume} />);
    expect(screen.queryByTestId("resume-banner")).toBeNull();
  });
});
