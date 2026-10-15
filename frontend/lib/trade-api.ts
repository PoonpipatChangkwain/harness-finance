/**
 * Trade + market endpoints (phase-10/11, contracts.md §11). The request body type is
 * generated (`TradeRunRequest`); the response dicts are typed here after the backend
 * (`app/api/routes/trade.py`, `market.py`, `domain/workflows/trading.py` state shapes).
 */

import { json, query, request } from "./api";
import type { components } from "./api-types";

export type TradeRunRequest = components["schemas"]["TradeRunRequest"];
export type AnalystName = NonNullable<TradeRunRequest["analysts"]>[number];

export interface GraphNodeView {
  id: string;
  label: string;
  kind: string;
  phase: string;
}
export interface GraphEdgeView {
  source: string;
  target: string;
  label?: string;
}
export interface GraphView {
  nodes: GraphNodeView[];
  edges: GraphEdgeView[];
}

export interface TradeRunBrief {
  run_id: string;
  symbol: string | null;
  date: string | null;
  rating: string | null;
  verdict: string | null;
  outcome: string | null;
  status: string;
  created_at: string;
}

/** `size_trade` output: every number computed (F1), never from an LLM. */
export interface TradeSizing {
  side: string;
  entry?: number | null;
  stop: number | null;
  targets?: number[];
  risk_reward?: (number | null)[];
  units: number;
  risk_amount?: number | null;
  notional?: number | null;
  position_weight?: number | null;
  risk_fraction?: number | null;
  equity?: number | null;
  currency?: string | null;
  notes?: string[];
  /** bars of the horizon's timeframe (15m / 1h / 1d) */
  interval?: string | null;
  time_stop_bars?: number | null;
  /** ISO deadline computed from the price's timestamp + bars × interval (null for a pending order) */
  time_stop_at?: string | null;
  /** "fill" for a pending order: the time stop counts from when it fills */
  time_stop_from?: "entry" | "fill" | null;
  /** pending entry: `entry` is the order price and `market_price` the price it waits from */
  order_type?: "market" | "limit" | "stop" | null;
  order?: TradeOrder | null;
  market_price?: number | null;
  order_distance_atr?: number | null;
  /** why the pending order is invalid (policy `valid_pending_order` blocks); null = valid */
  order_error?: string | null;
  order_valid_bars?: number | null;
  order_expires_at?: string | null;
}

export type TradeOrder = "market" | "buy_limit" | "sell_limit" | "buy_stop" | "sell_stop";

/** "sell_limit" → "SELL LIMIT"; null for a market order. */
export function orderLabel(order: string | null | undefined): string | null {
  return order && order !== "market" ? order.replace("_", " ").toUpperCase() : null;
}

/** One scheduled release (ForexFactory) as the backend returns it; `at` is UTC. */
export interface EconomicEventRow {
  at: string;
  hours_from_now: number;
  currency: string;
  impact: "high" | "medium" | "low" | "holiday";
  title: string;
  forecast: string | null;
  previous: string | null;
  actual: string | null;
}

/** state.event_risk: the calendar over the trade's holding period. */
export interface EventRisk {
  available: boolean;
  error?: string;
  source?: string;
  window_hours?: number;
  currencies?: string[];
  events?: EconomicEventRow[];
  next_high_impact_hours?: number | null;
}

/** state.ml_signal: the bull/bear model at the last closed bar (`trade_ml_signal`). */
export interface MlTrackStats {
  trades?: number | null;
  win_rate?: number | null;
  profit_factor?: number | null;
  expectancy_r?: number | null;
  long_share?: number | null;
}

export interface MlSignal {
  available: boolean;
  error?: string;
  model?: string;
  version?: string;
  timeframe?: string;
  bar_ts?: string;
  p_bull?: number;
  p_bear?: number;
  signal?: "long" | "short" | "flat";
  confidence?: number;
  daily_trend?: string;
  tp_r_long?: number;
  tp_r_short?: number;
  time_stop_bars?: number;
  is_nfp_day?: boolean;
  vix?: number | null;
  vix_level?: number | null;
  session?: string | null;
  missing_features?: string[];
  track_record?: { period?: string; model?: MlTrackStats; always_long?: MlTrackStats };
  provenance_ids?: string[];
}

export type OutcomeKind = "tp" | "sl" | "manual_exit" | "time_stop" | "not_filled" | "cancelled";

export interface TradeOutcomeBody {
  outcome: OutcomeKind;
  exit_price?: number | null;
  target_index?: number;
  note?: string;
}

/** A recorded (approved) decision and, once the trader ticks it, how it ended. */
export interface TradeResult {
  run_id: string;
  symbol: string;
  date: string | null;
  recorded_at: string | null;
  card: Record<string, unknown> | null;
  outcome: {
    kind: OutcomeKind;
    label: string;
    exit_price: number | null;
    r_multiple: number | null;
    note: string;
    settled_at: string;
    provenance_ids?: string[];
  } | null;
  reflection: string | null;
  reflection_error: string | null;
}

export interface PolicyResult {
  rule_id: string;
  passed: boolean;
  severity: "block" | "warn";
  message: string;
  values: Record<string, unknown>;
}

export interface PolicyEvaluation {
  blocked: boolean;
  results: PolicyResult[];
  attempts?: number;
}

export interface DecisionPayload {
  verdict: string;
  confidence: number;
  reason: string;
}

export interface FinalDecision {
  rating: string;
  decision: DecisionPayload;
  verdict: string;
  final_size_pct: number;
  adjustments: string;
  summary: string;
  sizing: TradeSizing;
  provenance_ids: string[];
  mode: string;
}

export interface DebateTurn {
  speaker: string;
  text: string;
}
export interface DebateState {
  history: DebateTurn[];
  count: number;
  latest_speaker: string | null;
}

/** Live price the plan was re-sized at (`trade_quote`). */
export interface LiveQuote {
  price: number;
  ts: string | null;
  provider: string | null;
  provenance_ids: string[];
}

/** state.live: the plan re-priced at the live quote just before approval. */
export interface LiveRepricing {
  refreshed: boolean;
  quote?: LiveQuote;
  snapshot_close?: number;
  sizing?: TradeSizing;
  provenance_ids?: string[];
  policy?: PolicyEvaluation;
}

export interface ApprovalState {
  outcome: "approved" | "rejected" | "edited";
  comment: string | null;
  edits: Record<string, unknown> | null;
  /** re-priced at the moment of approval (approved and edited alike) */
  sizing?: TradeSizing;
  provenance_ids?: string[];
  final_size_pct?: number;
  policy?: PolicyEvaluation;
  quote?: LiveQuote | null;
  entry_drift_atr?: number;
}

export interface Instrument {
  valid: boolean;
  raw: string;
  canonical?: string;
  display?: string;
  asset_type?: string;
  market?: string;
  currency?: string;
  lot_size?: number;
  analysts?: AnalystName[];
  analysis_date?: string | null;
  error?: string;
}

export interface TradeRunDetail {
  run: {
    id: string;
    status: string;
    input: Record<string, unknown>;
    thread_id: string | null;
    started_at: string;
    finished_at: string | null;
    usage: Record<string, unknown>;
    error?: string | null;
  };
  input: Record<string, unknown>;
  graph_view: GraphView;
  nodes: Record<string, { status?: string; runs?: number; duration_ms?: number; summary?: string | null }>;
  instrument: Instrument | null;
  market_snapshot: {
    available: boolean;
    as_of?: string;
    currency?: string;
    close?: number;
    indicators?: Record<string, number>;
    provenance_ids?: string[];
  } | null;
  reports: Record<string, { status: string; text: string; error?: string; stop_reason?: string | null }> | null;
  invest_debate: DebateState | null;
  research_plan: Record<string, unknown> | null;
  trade_proposal: { proposal: Record<string, unknown>; sizing: TradeSizing; provenance_ids: string[] } | null;
  risk_debate: DebateState | null;
  decision: FinalDecision | null;
  policy: PolicyEvaluation | null;
  live?: LiveRepricing | null;
  event_risk?: EventRisk | null;
  /** null when no validated model covers this symbol/horizon (or the run opted out) */
  ml_signal?: MlSignal | null;
  /** the decision log entry of an approved run (null until recorded) */
  trade_result?: TradeResult | null;
  /** state.approval once answered, else the pending approval row */
  approval: ApprovalState | Record<string, unknown> | null;
  outcome: { status: string; reason?: string | null; rating?: string | null; verdict?: string | null } | null;
  report_md: string | null;
}

/** The answered approval of a run (`state.approval`), not a still-pending approval row. */
export function answeredApproval(detail: TradeRunDetail | undefined): ApprovalState | null {
  const a = detail?.approval as Partial<ApprovalState> | null | undefined;
  return a && (a.outcome === "approved" || a.outcome === "rejected" || a.outcome === "edited") ? (a as ApprovalState) : null;
}

export interface OhlcvBar {
  ts: string;
  open: string | number;
  high: string | number;
  low: string | number;
  close: string | number;
  volume: number;
}

export interface Ohlcv {
  symbol: string;
  currency: string;
  asset_type: string;
  bars: OhlcvBar[];
  provider: string;
  provenance_id: string | null;
}

export type IndicatorSet = "sma20" | "sma50" | "sma200" | "bb20" | "rsi14" | "macd";

export interface Indicators {
  symbol: string;
  ts: string[];
  series: Record<string, (number | null)[]>;
  provenance: Record<string, string>;
  price_provenance_id: string | null;
  provider: string;
}

export type ChartRange = "1d" | "5d" | "1mo" | "3mo" | "6mo" | "1y" | "5y";
export type ChartInterval = "1m" | "5m" | "15m" | "1h" | "1d";

export const tradeApi = {
  start: (body: TradeRunRequest) => request<components["schemas"]["TradeRunStarted"]>("/trade/runs", json(body)),
  list: (symbol?: string) => request<{ runs: TradeRunBrief[] }>(`/trade/runs${query({ symbol })}`),
  detail: (runId: string) => request<TradeRunDetail>(`/trade/runs/${runId}`),
  delete: (runId: string) =>
    request<components["schemas"]["RunDeleted"]>(`/trade/runs/${runId}`, { method: "DELETE" }),
  graph: () => request<GraphView>("/workflows/trading/graph"),
  state: (runId: string) =>
    request<{ values: Record<string, unknown>; next: string[]; thread_id: string }>(`/runs/${runId}/state`),
  settle: (runId: string, body: TradeOutcomeBody) =>
    request<TradeResult>(`/trade/runs/${runId}/outcome`, json(body)),
  decisions: (symbol?: string, openOnly = false) =>
    request<{ decisions: TradeResult[] }>(`/trade/decisions${query({ symbol, open_only: openOnly ? "true" : undefined })}`),
  ohlcv: (symbol: string, interval: ChartInterval, range: ChartRange, asOf?: string | null) =>
    request<Ohlcv>(`/market/${encodeURIComponent(symbol)}/ohlcv${query({ interval, range, as_of: asOf })}`),
  indicators: (symbol: string, sets: IndicatorSet[], interval: ChartInterval, range: ChartRange, asOf?: string | null) =>
    request<Indicators>(
      `/market/${encodeURIComponent(symbol)}/indicators${query({ set: sets.join(","), interval, range, as_of: asOf })}`,
    ),
};
