import { describe, expect, it } from "vitest";

import { buildEdits, editSchema } from "@/components/trade/TradeApprovalPanel";
import { defaultSetup, isBacktest, toRunRequest } from "@/components/trade/RunSetupBar";
import { mapCitations } from "@/components/trade/DecisionCard";
import { debateRounds } from "@/components/trade/DebateView";
import type { ProvenanceRecord } from "@/lib/api";
import { allowedAnalysts, assetBadge, buildPipeline, debateRound, defaultAnalysts } from "@/lib/trade";
import { loadTradingGraph } from "@/tests/fixtures";

const graph = loadTradingGraph();

describe("buildPipeline", () => {
  it("expands the analysts node into the selected analysts", () => {
    const { nodes, edges } = buildPipeline(graph, ["market", "news", "macro"]);
    const ids = nodes.map((n) => n.id);
    expect(ids).toContain("analyst.macro");
    expect(ids).not.toContain("analyst.fundamentals");
    expect(ids).not.toContain("analysts");
    expect(edges.map((e) => e.id)).toContain("load_context->analyst.news");
    expect(edges.map((e) => e.id)).toContain("analyst.macro->bull_researcher");
  });

  it("drops END, self loops and fallbacks; keeps conditional edges labelled", () => {
    const { edges } = buildPipeline(graph, ["market"]);
    expect(edges.some((e) => e.target === "END" || e.source === e.target)).toBe(false);
    expect(edges.some((e) => e.label?.includes("__fallback__"))).toBe(false);
    const retry = edges.find((e) => e.id === "policy_gate->trader");
    expect(retry).toMatchObject({ conditional: true, label: "retry" });
    expect(edges.find((e) => e.id === "record_decision->report")?.conditional).toBe(false);
  });
});

describe("asset types and rounds", () => {
  it("follows the backend defaults per asset type", () => {
    expect(defaultAnalysts("stock")).toEqual(["market", "news", "fundamentals", "sentiment"]);
    expect(defaultAnalysts("forex")).toEqual(["market", "news", "macro"]);
    expect(allowedAnalysts("forex")).not.toContain("fundamentals");
    expect(assetBadge({ asset_type: "stock", market: "TH" })).toBe("stock·TH");
    expect(assetBadge({ asset_type: "forex", market: "GLOBAL" })).toBe("forex");
  });

  it("labels debate rounds", () => {
    expect(debateRound("bull_researcher", 2, { debate_rounds: 3 })).toEqual({ round: 2, of: 3 });
    expect(debateRound("neutral", 1, {})).toEqual({ round: 1, of: 1 });
    expect(debateRound("trader", 1, {})).toBeNull();
    expect(debateRounds([
      { speaker: "bull", text: "a" },
      { speaker: "bear", text: "b" },
      { speaker: "bull", text: "c" },
    ], ["bull", "bear"])).toEqual([{ bull: "a", bear: "b" }, { bull: "c" }]);
  });
});

describe("run setup → request body", () => {
  const eurusd = { raw: "EURUSD", canonical: "EURUSD=X", asset_type: "forex", market: "GLOBAL", currency: "USD", display: "EUR/USD" };

  it("today means latest; a past date is a backtest", () => {
    const form = { ...defaultSetup("forex", "2026-09-26"), analysts: ["market", "fundamentals", "macro"] as const };
    const body = toRunRequest(eurusd, { ...form, analysts: [...form.analysts] }, null, "2026-09-26");
    expect(body).toMatchObject({ symbol: "EURUSD=X", analysis_date: null, analysts: ["market", "macro"], llm: null });
    expect(toRunRequest(eurusd, { ...defaultSetup("forex"), date: "2026-01-02" }, null, "2026-09-26").analysis_date).toBe(
      "2026-01-02",
    );
    expect(isBacktest("2026-09-25", "2026-09-26")).toBe(true);
  });
});

describe("approval edit → request body", () => {
  it("sends only the filled basis fields, size as a fraction", () => {
    const empty = { sizePct: "", stop: "", targets: "", orderPrice: "" };
    const form = editSchema.parse({ ...empty, sizePct: "50", stop: "33", targets: "37, 39.5" });
    expect(buildEdits(form)).toEqual({ final_size_pct: 0.5, stop_level: 33, target_levels: [37, 39.5] });
    expect(buildEdits(editSchema.parse({ ...empty, stop: "32.5" }))).toEqual({ stop_level: 32.5 });
    expect(buildEdits(editSchema.parse({ ...empty, orderPrice: "4188" }))).toEqual({ order_price: 4188 });
  });

  it("rejects bad input", () => {
    const empty = { sizePct: "", stop: "", targets: "", orderPrice: "" };
    expect(editSchema.safeParse({ ...empty, sizePct: "150" }).success).toBe(false);
    expect(editSchema.safeParse({ ...empty, stop: "abc" }).success).toBe(false);
    expect(editSchema.safeParse({ ...empty, targets: "37,,x" }).success).toBe(false);
    expect(editSchema.safeParse({ ...empty, orderPrice: "-1" }).success).toBe(false);
  });
});

describe("mapCitations", () => {
  const rec = (id: string, calculation: string) => ({ id, calculation }) as ProvenanceRecord;
  it("maps each compute record to its number", () => {
    const c = mapCitations(
      [
        rec("p_stop", "compute.risk.stop_from_atr@v1"),
        rec("p_rr1", "compute.risk.risk_reward@v1"),
        rec("p_rr2", "compute.risk.risk_reward@v1"),
        rec("p_units", "compute.risk.position_size@v1"),
        rec("p_size", "compute.trade.size@v1"),
        rec("p_ts", "compute.trade.time_stop@v1"),
        rec("p_order", "compute.trade.order@v1"),
        rec("p_exp", "compute.trade.order_expiry@v1"),
        null,
      ],
      "p_snap",
    );
    expect(c).toEqual({
      entry: "p_snap",
      order: "p_order",
      stop: "p_stop",
      riskReward: ["p_rr1", "p_rr2"],
      units: "p_units",
      size: "p_size",
      timeStop: "p_ts",
    });
  });
});
