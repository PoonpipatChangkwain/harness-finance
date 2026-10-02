/**
 * Backtest endpoints (P15, contracts.md §11). Request bodies are generated
 * (`BacktestConfig`, `SimConfig`); responses are typed here after
 * `backend/app/api/routes/backtest.py` and `app/domain/compute/backtest/models.py`.
 * Money comes as decimal strings (the backend keeps `Decimal`); ratios as numbers.
 */

import { json, request } from "./api";
import type { components } from "./api-types";

export type BacktestConfig = components["schemas"]["BacktestConfig"];
export type SimConfigBody = components["schemas"]["SimConfig"];

export interface BacktestEstimate {
  cells: number;
  cached: number;
  to_run: number;
  approximate: boolean;
  limit: number;
  per_run: { cost_usd: number | null; llm_calls: number | null; minutes: number | null };
  based_on_runs: number;
  est_cost_usd: number | null;
  est_llm_calls: number | null;
  est_minutes: number | null;
}

export interface BacktestBrief {
  backtest_id: string;
  name: string;
  symbols: string[];
  start_date: string;
  end_date: string;
  horizon: string;
  status: string;
  created_at: string;
  final_equity: string | null;
  total_return: number | null;
  currency: string | null;
  cost_usd: number | null;
}

export interface CellDecision {
  side: "long" | "short" | "flat";
  outcome?: string | null;
  reason?: string | null;
  rating?: string | null;
  verdict?: string | null;
  stop?: number | null;
  targets?: number[];
  order_type?: string | null;
  size_fraction?: number | null;
}

export interface BacktestCell {
  id: string;
  symbol: string;
  day: string;
  status: "pending" | "running" | "done" | "failed";
  run_id: string | null;
  cached_from: string | null;
  decision: CellDecision | null;
  usage: { cost_usd?: number };
  error: string | null;
}

export interface SimMetrics {
  initial_capital: string;
  final_equity: string;
  profit: string;
  currency: string;
  total_return: number;
  cagr: number | null;
  max_drawdown: number;
  sharpe: number;
  sortino: number;
  exposure: number;
  first_day: string | null;
  last_day: string | null;
  trades: number;
  win_rate: number | null;
  avg_win: string | null;
  avg_loss: string | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  avg_bars_held: number | null;
  fees: string;
  exits: Record<string, number>;
  skipped: Record<string, number>;
  benchmark_return?: number | null;
  alpha?: number | null;
}

export interface SimTrade {
  symbol: string;
  side: "long" | "short";
  ref: string;
  decided_on: string;
  entry_at: string;
  entry_price: string;
  exit_at: string;
  exit_price: string;
  units: string;
  stop: string;
  exit_reason: "tp" | "sl" | "time_stop" | "reverse" | "end_of_test";
  gap: boolean;
  fees: string;
  pnl: string;
  r_multiple: string | null;
  bars_held: number;
}

export interface SimSkipped {
  symbol: string;
  ref: string;
  decided_on: string;
  reason: string;
  detail: string;
}

export interface EquityPoint {
  day: string;
  equity: string;
  cash?: string;
  gross_exposure?: string;
  open_positions?: number;
}

export interface SimResult {
  trades: SimTrade[];
  skipped: SimSkipped[];
  equity_curve: EquityPoint[];
  benchmark: EquityPoint[];
  intervals: Record<string, string>;
  baseline?: { name: string; metrics: SimMetrics; equity_curve: EquityPoint[] };
}

export interface SimBrief {
  sim_id: string;
  created_at: string;
  config: Record<string, unknown>;
  metrics: SimMetrics;
}

export interface Sim extends SimBrief {
  result: SimResult;
}

export interface BacktestDetail extends BacktestBrief {
  run: { id: string; status: string; error?: string | null; started_at: string; finished_at: string | null };
  config: BacktestConfig & Record<string, unknown>;
  output: { cells?: Record<string, number>; pending?: number; cost_usd?: number; budget_hit?: boolean } | null;
  running: boolean;
  cells: BacktestCell[];
  sims: SimBrief[];
  latest_sim: Sim | null;
}

export const backtestApi = {
  estimate: (body: BacktestConfig) => request<BacktestEstimate>("/backtests/estimate", json(body)),
  start: (body: BacktestConfig) => request<{ backtest_id: string }>("/backtests", json(body)),
  list: () => request<{ backtests: BacktestBrief[] }>("/backtests"),
  detail: (id: string) => request<BacktestDetail>(`/backtests/${id}`),
  simulate: (id: string, body: SimConfigBody) => request<Sim>(`/backtests/${id}/simulate`, json(body)),
  sim: (id: string, simId: string) => request<Sim>(`/backtests/${id}/sims/${simId}`),
  remove: (id: string) => request<{ deleted: string }>(`/backtests/${id}`, { method: "DELETE" }),
};
