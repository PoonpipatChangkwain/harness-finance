import { describe, expect, it } from "vitest";

import {
  applyCellEvent,
  cellMatrix,
  cellTone,
  defaultForm,
  drawdown,
  formProblems,
  mergeCurves,
  pctToFraction,
  simFormFrom,
  toConfig,
  toSimConfig,
} from "@/lib/backtest";
import type { BacktestCell } from "@/lib/backtest-api";

const NOW = new Date("2026-10-01T12:00:00");

function cell(over: Partial<BacktestCell>): BacktestCell {
  return {
    id: "c1", symbol: "PTT.BK", day: "2026-08-03", status: "done", run_id: "run_1", cached_from: null,
    decision: { side: "long" }, usage: {}, error: null, ...over,
  };  // prettier-ignore
}

describe("form → request", () => {
  it("turns percent text into fractions and drops blanks", () => {
    expect(pctToFraction("1")).toBe("0.01");
    expect(pctToFraction("0.157")).toBe("0.00157");
    expect(pctToFraction("")).toBeUndefined();
    expect(pctToFraction("abc")).toBeUndefined();
    const sim = toSimConfig({ ...defaultForm(NOW).sim, capital: "1,000,000", riskPct: "2", maxWeightPct: "", commissionPct: "0.1" });
    expect(sim).toMatchObject({ initial_capital: "1000000", risk_pct: "0.02", max_position_weight: null, commission_pct: "0.001" });
  });

  it("builds the BacktestConfig: auto cadence is null, no budget is null", () => {
    const f = { ...defaultForm(NOW), symbols: ["PTT.BK"], cadence: "", budgetUsd: "" };
    const cfg = toConfig(f, { provider: "groq", model: "m" });
    expect(cfg).toMatchObject({ symbols: ["PTT.BK"], cadence: null, budget_usd: null, horizon: "swing", llm: { provider: "groq", model: "m" } });
    expect(toConfig({ ...f, cadence: "5", budgetUsd: "2.5" }, null)).toMatchObject({ cadence: 5, budget_usd: 2.5, llm: null });
  });

  it("round-trips a stored SimConfig back into the form", () => {
    const stored = { initial_capital: "500000", base_currency: "USD", risk_pct: "0.015", max_position_weight: null,
      max_open_positions: 3, commission_pct: "0.00157", slippage_bps: "5", compounding: false, shortable: [] };  // prettier-ignore
    const f = simFormFrom(stored);
    expect(f).toMatchObject({ capital: "500000", baseCurrency: "USD", riskPct: "1.5", maxWeightPct: "", commissionPct: "0.157", compounding: false, allowShortForex: false });
    expect(toSimConfig(f)).toMatchObject({ risk_pct: "0.015", commission_pct: "0.00157", max_position_weight: null });
  });

  it("flags what the server would reject", () => {
    const today = "2026-10-01";
    const problems = formProblems({ ...defaultForm(NOW), symbols: [], start: today, end: "2026-09-01", analysts: [] }, today);
    expect(problems).toEqual(
      expect.arrayContaining(["เลือก symbol อย่างน้อย 1 ตัว", "วันเริ่มต้องเป็นวันในอดีต", "วันสิ้นสุดต้องไม่ก่อนวันเริ่ม", "เลือก analyst อย่างน้อย 1 ตัว"]),
    );
  });
});

describe("cell grid", () => {
  it("colours a cell by its status, then by the side it decided", () => {
    expect(cellTone(cell({ status: "running", decision: null }))).toBe("running");
    expect(cellTone(cell({ decision: { side: "short" } }))).toBe("short");
    expect(cellTone(cell({ decision: { side: "flat" } }))).toBe("flat");
    expect(cellTone(cell({ status: "failed" }))).toBe("failed");
  });

  it("lays cells out symbol × day", () => {
    const m = cellMatrix([cell({ id: "a", day: "2026-08-04" }), cell({ id: "b", symbol: "AOT.BK", day: "2026-08-03" })]);
    expect(m.symbols).toEqual(["AOT.BK", "PTT.BK"]);
    expect(m.days).toEqual(["2026-08-03", "2026-08-04"]);
    expect(m.at("PTT.BK", "2026-08-03")).toBeNull();
    expect(m.at("AOT.BK", "2026-08-03")?.id).toBe("b");
  });

  it("applies live cell events, appending unknown cells", () => {
    const cells = [cell({ status: "pending", decision: null, run_id: null })];
    const running = applyCellEvent(cells, { cell_id: "c1", symbol: "PTT.BK", day: "2026-08-03", status: "running", run_id: "run_9", cached: false });
    expect(running[0]).toMatchObject({ status: "running", run_id: "run_9", decision: null });
    const done = applyCellEvent(running, { cell_id: "c1", symbol: "PTT.BK", day: "2026-08-03", status: "done", side: "long", rating: "Buy", cached: false });
    expect(done[0]?.decision).toMatchObject({ side: "long", rating: "Buy" });
    const added = applyCellEvent(done, { cell_id: "c2", symbol: "AOT.BK", day: "2026-08-03", status: "done", side: "flat", cached: true });
    expect(added).toHaveLength(2);
    expect(added[1]).toMatchObject({ id: "c2", cached_from: "cache" });
  });
});

describe("chart series", () => {
  const pts = (vals: number[]) => vals.map((v, i) => ({ day: `2026-08-0${i + 1}`, equity: String(v) }));

  it("merges curves by day, in money or % of each line's start", () => {
    const money = mergeCurves(pts([100, 110]), pts([200, 180]));
    expect(money).toEqual([
      { day: "2026-08-01", strategy: 100, benchmark: 200 },
      { day: "2026-08-02", strategy: 110, benchmark: 180 },
    ]);
    const pct = mergeCurves(pts([100, 110]), pts([200, 180]), [], "percent");
    expect(pct[1]?.strategy).toBeCloseTo(10);
    expect(pct[1]?.benchmark).toBeCloseTo(-10);
  });

  it("computes drawdown below the running peak", () => {
    expect(drawdown(pts([100, 120, 90, 130])).map((p) => Math.round(p.dd))).toEqual([0, 0, -25, 0]);
  });
});
