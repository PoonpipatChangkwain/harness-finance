import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestSetup } from "@/components/backtest/BacktestSetup";
import { CellGrid } from "@/components/backtest/CellGrid";
import { ResultTiles } from "@/components/backtest/ResultTiles";
import { SimFields } from "@/components/backtest/SimFields";
import { TradesTable } from "@/components/backtest/TradesTable";
import { DEFAULT_SIM } from "@/lib/backtest";
import { backtestApi, type SimMetrics, type SimResult } from "@/lib/backtest-api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/backtest" }));
vi.mock("@/components/conversation/ModelPicker", () => ({
  ModelPicker: () => null,
  KeyWarning: () => null,
  choiceProblem: () => null,
  useLlmProviders: () => ({ data: null, error: false, refresh: () => {} }),
}));
vi.mock("@/components/trade/SymbolSearch", () => ({
  SymbolSearch: ({ onPick }: { onPick: (o: unknown) => void }) => (
    <button type="button" data-testid="pick-ptt" onClick={() => onPick({ symbol: { canonical: "PTT.BK" }, name: "PTT" })}>
      pick
    </button>
  ),
}));

afterEach(() => vi.restoreAllMocks());

const METRICS: SimMetrics = {
  initial_capital: "100000.00", final_equity: "112345.67", profit: "12345.67", currency: "THB", total_return: 0.1234567,
  cagr: 0.3, max_drawdown: -0.08, sharpe: 1.2, sortino: 1.8, exposure: 0.4, first_day: "2026-07-01", last_day: "2026-09-30",
  trades: 7, win_rate: 0.571, avg_win: "3000.00", avg_loss: "-1000.00", profit_factor: 2.1, expectancy_r: 0.6,
  avg_bars_held: 12, fees: "250.00", exits: { tp: 4, sl: 3 }, skipped: { below_one_lot: 2, flat: 5 },
  benchmark_return: 0.05, alpha: 0.0734567,
};  // prettier-ignore

describe("SimFields", () => {
  it("warns while the commission is 0 and edits values", () => {
    const onChange = vi.fn();
    render(<SimFields value={DEFAULT_SIM} onChange={onChange} />);
    expect(screen.getByTestId("no-fee-warning")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("sim-capital"), { target: { value: "500000" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ capital: "500000" }));
  });

  it("hides the warning once a commission is set", () => {
    render(<SimFields value={{ ...DEFAULT_SIM, commissionPct: "0.157" }} onChange={() => {}} />);
    expect(screen.queryByTestId("no-fee-warning")).toBeNull();
  });
});

describe("CellGrid", () => {
  it("draws one square per cell, linked to its trading run", () => {
    render(
      <CellGrid
        cells={[
          { id: "a", symbol: "PTT.BK", day: "2026-08-03", status: "done", run_id: "run_a", cached_from: null, decision: { side: "long" }, usage: {}, error: null },
          { id: "b", symbol: "PTT.BK", day: "2026-08-06", status: "running", run_id: "run_b", cached_from: null, decision: null, usage: {}, error: null },
          { id: "c", symbol: "AOT.BK", day: "2026-08-03", status: "pending", run_id: null, cached_from: null, decision: null, usage: {}, error: null },
        ]}
      />,
    );
    const tones = screen.getAllByTestId("cell").map((c) => c.dataset.tone);
    expect(tones.sort()).toEqual(["long", "pending", "running"]);
    expect(document.querySelector('a[href="/trade/run_a"]')).not.toBeNull();
  });
});

describe("ResultTiles + TradesTable", () => {
  it("shows the simulator's numbers", () => {
    render(<ResultTiles m={METRICS} />);
    expect(screen.getByTestId("tile-final").textContent).toContain("112,346");
    expect(screen.getByTestId("result-tiles").textContent).toContain("+12.35%");
  });

  it("lists trades and why the rest did not trade", () => {
    const result: SimResult = {
      trades: [
        { symbol: "PTT.BK", side: "long", ref: "run_a", decided_on: "2026-08-03", entry_at: "2026-08-04T00:00:00Z", entry_price: "34.5",
          exit_at: "2026-08-10T00:00:00Z", exit_price: "37", units: "500", stop: "33.3", exit_reason: "tp", gap: false, fees: "0",
          pnl: "1250", r_multiple: "2.0833", bars_held: 4 },
      ],  // prettier-ignore
      skipped: [{ symbol: "PTT.BK", ref: "run_b", decided_on: "2026-08-06", reason: "below_one_lot", detail: "" }],
      equity_curve: [],
      benchmark: [],
      intervals: {},
    };
    render(<TradesTable result={result} />);
    expect(screen.getByTestId("trades-table").textContent).toContain("ชน target");
    expect(screen.getByRole("link", { name: "PTT.BK" })).toHaveAttribute("href", "/trade/run_a");
    expect(screen.getByTestId("skipped-summary").textContent).toContain("เงินไม่พอซื้อ 1 lot");
  });
});

describe("BacktestSetup", () => {
  it("estimates first, then starts with the form as a BacktestConfig", async () => {
    const estimate = vi.spyOn(backtestApi, "estimate").mockResolvedValue({
      cells: 60, cached: 10, to_run: 50, approximate: true, limit: 400,
      per_run: { cost_usd: 0.02, llm_calls: 20, minutes: 2 }, based_on_runs: 5,
      est_cost_usd: 1, est_llm_calls: 1000, est_minutes: 50,
    });  // prettier-ignore
    const start = vi.spyOn(backtestApi, "start").mockResolvedValue({ backtest_id: "run_bt" });
    const onStarted = vi.fn();
    render(<BacktestSetup onStarted={onStarted} />);

    const startBtn = screen.getByTestId("bt-start-run");
    expect(startBtn).toBeDisabled(); // no estimate yet
    expect(screen.getByTestId("bt-estimate")).toBeDisabled(); // no symbol yet
    fireEvent.click(screen.getByTestId("pick-ptt"));
    fireEvent.click(screen.getByTestId("bt-estimate"));
    await waitFor(() => expect(screen.getByTestId("bt-estimate-result").textContent).toContain("50"));
    expect(estimate.mock.calls[0]?.[0]).toMatchObject({ symbols: ["PTT.BK"], sim: { initial_capital: "100000", risk_pct: "0.01" } });

    fireEvent.click(startBtn);
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("run_bt"));
    expect(start).toHaveBeenCalledTimes(1);
  });
});
