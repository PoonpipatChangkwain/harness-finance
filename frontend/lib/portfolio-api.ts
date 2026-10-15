/**
 * Portfolio endpoints (phase-12/13, contracts.md §11). The backend returns plain dicts for
 * analytics / summary / plans, so the shapes are typed here after
 * `app/domain/portfolio/analytics.py`, `summary.py`, `app/api/routes/portfolio.py` and the
 * advisor's `plan_card`. Money arrives as Decimal strings on the book itself and as numbers
 * (with a provenance id) in analytics — the UI only formats them (F1).
 */

import { API_BASE, ApiError, json, query, request, type LlmChoice, type SymbolInfo } from "./api";

/** A computed number and the provenance record behind it. */
export interface PV {
  value: number;
  provenance_id: string;
}

export interface PositionOut {
  symbol: SymbolInfo;
  quantity: string;
  avg_cost: string | null;
  target_weight: string | null;
  declared_weight: string | null;
  note: string | null;
}

export interface PortfolioData {
  id: string;
  name: string;
  base_currency: string;
  cash: string | null;
  positions: PositionOut[];
  updated_at: string;
  icon: string | null;
  color: string | null;
  provenance_id: string | null;
}

export type PortfolioState = "positions" | "flat" | "not_provided";

export interface PortfolioOut {
  portfolio: PortfolioData;
  state: PortfolioState;
  portfolio_hash: string;
}

export interface PortfolioBrief {
  id: string;
  name: string;
  base_currency: string;
  icon: string | null;
  color: string | null;
  cash: string | null;
  positions: number;
  updated_at: string;
}

/**
 * How a position was entered (contracts §5, P16). The backend fills in what is missing with
 * compute (never the frontend): e.g. `qty_pnl_pct` → avg cost from the latest price.
 */
export type EntryMode =
  | "qty_cost"
  | "qty_pnl_pct"
  | "qty_pnl_amount"
  | "value_pnl_pct"
  | "value_pnl_amount"
  | "cost_total"
  | "weight";

/**
 * Backend `PositionInput`. Numbers are decimal strings; `pnl_pct` / `weight` / `target_weight`
 * are fractions (14.52% → "0.1452"); `pnl_pct` / `pnl_amount` are negative for a loss and
 * `pnl_amount` is money in the symbol's currency. Only the fields of `mode` may be set.
 */
export interface PositionInput {
  symbol: string;
  mode?: EntryMode;
  quantity?: string;
  avg_cost?: string;
  weight?: string;
  target_weight?: string;
  value?: string;
  pnl_pct?: string;
  pnl_amount?: string;
  cost_total?: string;
  note?: string;
}

export interface PortfolioWrite {
  name?: string;
  base_currency?: string;
  icon?: string | null;
  color?: string | null;
  cash?: string | null;
  positions: PositionInput[];
}

export interface EntryError {
  index: number | null;
  symbol: string | null;
  field: string | null;
  message: string;
}

export interface HoldingAnalytics {
  symbol: string;
  raw: string;
  display: string;
  asset_type: string;
  market: string;
  currency: string;
  sector: string;
  quantity: number;
  avg_cost: number | null;
  declared_weight: number | null;
  target_weight: number | null;
  note: string | null;
  status: "ok" | "no_price" | "no_fx" | "declared";
  price: PV | null;
  prev_close: number | null;
  fx_rate: PV | null;
  market_value_local: PV | null;
  market_value: PV | null;
  cost_basis: PV | null;
  weight: PV | null;
  unrealized_pnl: PV | null;
  unrealized_pnl_pct: PV | null;
  day_pnl: PV | null;
  day_change_pct: PV | null;
  risk_contribution: PV | null;
}

export interface RiskMetrics {
  benchmark: string | null;
  observations: number;
  start: string;
  end: string;
  volatility: PV;
  max_drawdown: PV;
  beta: PV | null;
  var_95: PV;
  cvar_95: PV;
  correlation: Record<string, Record<string, number>>;
  correlation_provenance_id: string;
  contribution: Record<string, number>;
  contribution_provenance_id: string;
  method: string;
}

export interface AnalyticsWarning {
  code: string;
  message: string;
  symbol: string | null;
}

export interface PortfolioAnalytics {
  portfolio_id: string;
  name: string;
  base_currency: string;
  portfolio_hash: string;
  computed_at: string;
  as_of: string | null;
  weights_declared_only: boolean;
  total_value: PV | null;
  cash: PV | null;
  invested_value: PV | null;
  cost_basis: PV | null;
  unrealized_pnl: PV | null;
  unrealized_pnl_pct: PV | null;
  day_pnl: PV | null;
  day_pnl_pct: PV | null;
  positions_count: number;
  holdings: HoldingAnalytics[];
  weights: Record<string, number>;
  weights_provenance_id: string | null;
  concentration: { hhi: number; top1: number; top3: number; effective_n: number; provenance_id: string } | null;
  exposure: Record<string, Record<string, number>>;
  exposure_provenance: Record<string, string>;
  risk: RiskMetrics | null;
  fx: Record<string, PV>;
  warnings: AnalyticsWarning[];
}

export type MarketFilter = "ALL" | "TH" | "US";

export interface SummaryPortfolio {
  id: string;
  name: string;
  icon: string | null;
  color: string | null;
  base_currency: string;
  value: number | null;
  cash?: number;
  cost_basis?: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  holdings?: number;
  weight: number | null;
}

export interface SummaryAsset {
  symbol: string;
  display: string;
  market: string;
  asset_type: string;
  currency: string;
  sector: string;
  quantity: number;
  value: number;
  weight: number | null;
  cost_basis: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  day_change_pct: number | null;
  portfolios: string[];
}

export interface SummaryView {
  currency: string;
  market: MarketFilter;
  total_value: number;
  cash: number;
  cost_basis: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  day_pnl: number | null;
  day_pnl_pct: number | null;
  portfolios: SummaryPortfolio[];
  assets: SummaryAsset[];
  warnings: string[];
  provenance_id: string;
  fx: Record<string, { rate: number; provenance_id: string | null }>;
}

export interface PortfolioSummary extends SummaryView {
  alt: SummaryView | null;
}

/** A row of the advisor's rebalance card (`plan_card` in domain/workflows/portfolio_advisor.py). */
export interface ProposalRow {
  symbol: string;
  current: number;
  target: number;
  after: number;
  delta: number;
  action: "buy" | "sell" | "hold" | string;
  shares: number;
  value: number;
  rationale: string;
}

export interface PlanMetrics {
  hhi: number | null;
  top1: number | null;
  effective_n: number | null;
  cash_weight: number | null;
  max_sector: number | null;
}

export interface ProposalCard {
  portfolio_id: string;
  name: string;
  base_currency: string;
  total_value: number | null;
  rows: ProposalRow[];
  cash_before: number | null;
  cash_after: number | null;
  cash_target: number | null;
  turnover: number | null;
  metrics_before: PlanMetrics;
  metrics_after: PlanMetrics;
  summary: string | null;
  verdict: string | null;
  confidence: number;
  warnings: string[];
  provenance: Record<string, string>;
}

export interface PlanRecord {
  id: string;
  portfolio_id: string;
  run_id: string | null;
  status: "approved" | "applied" | string;
  proposal: {
    card?: ProposalCard;
    targets?: Record<string, number>;
    cash_target?: number;
    legs?: Record<string, unknown>[];
    portfolio_hash?: string;
  };
  response: { outcome: string; comment: string | null; edits: Record<string, unknown> | null } | null;
  created_at: string;
  approved_at: string | null;
  applied_at: string | null;
  applied_revision_id: string | null;
}

export interface ApplyDiff {
  positions: { symbol: string; before: string; after: string; change: string }[];
  cash_before: string | null;
  cash_after: string | null;
}

export interface RevisionRecord {
  id: string;
  portfolio_id: string;
  reason: string;
  snapshot: Record<string, unknown>;
  provenance_id: string | null;
  plan_id: string | null;
  created_at: string;
}

export interface ImportPreview {
  rows: number;
  positions: PositionOut[];
  converted: Record<string, string | number>[];
  errors: EntryError[];
  committed: boolean;
  revision_id?: string;
}

export interface WeeklyReport {
  id: string;
  portfolio_id: string;
  kind: string;
  taken_at: string;
  summary: {
    base_currency?: string;
    as_of?: string | null;
    total_value?: PV | null;
    unrealized_pnl?: PV | null;
    unrealized_pnl_pct?: PV | null;
    day_pnl_pct?: PV | null;
    volatility?: PV | null;
    max_drawdown?: PV | null;
    warnings?: string[];
    error?: string;
    run_error?: string;
  };
  run_id: string | null;
  session_id: string | null;
  run_status: string | null;
}

export interface WeeklyReports {
  schedule: { cron: string; enabled: boolean; last_run: string | null; last_slot: string; next_run: string };
  reports: WeeklyReport[];
}

export type RiskProfile = "conservative" | "balanced" | "aggressive";

export interface AdviseRequest {
  goal?: string | null;
  risk_profile?: RiskProfile;
  constraints?: { max_weight?: number; max_sector?: number; min_cash?: number; no_sell?: string[]; max_turnover?: number };
  language?: "th" | "en";
  llm?: LlmChoice | null;
}

export interface AdvisorRun {
  run_id: string;
  status: string;
  active: boolean;
  started_at: string;
  finished_at: string | null;
  risk_profile: RiskProfile | null;
  outcome: string | null;
  plan_id: string | null;
  report_md: string | null;
}

/** One row of `POST …/positions/preview` (P16): the resolved entry, nothing saved. */
export interface PreviewRow {
  index: number;
  symbol: SymbolInfo;
  mode: EntryMode;
  status: "ok" | "error";
  quantity: string | null;
  avg_cost: string | null;
  price: PV | null;
  price_as_of: string | null;
  currency: string;
  market_value: PV | null;
  cost_basis: PV | null;
  unrealized_pnl: PV | null;
  unrealized_pnl_pct: PV | null;
  provenance_id: string | null;
  /** status "error" → why the row cannot be saved; status "ok" → warnings (e.g. no price) */
  errors: EntryError[];
}

export interface PositionsPreview {
  rows: PreviewRow[];
  /** book-level errors */
  errors: EntryError[];
}

export type MarketHint = "TH" | "US";

export interface ParseHoldingsRequest {
  text: string;
  market_hint?: MarketHint | null;
  llm?: LlmChoice | null;
}

export interface ParsedHoldings {
  rows: PositionInput[];
  unparsed: string[];
  mode: "native" | "fallback_parsed" | "failed";
  provider: string | null;
  model: string | null;
}

/** `GET /market/{symbol}/quote` */
export interface QuoteOut {
  quote: { symbol: string; price: string; change: string | null; change_pct: string | null; ts: string; currency: string };
  provider: string;
  provenance_id: string | null;
}

type Loose = Record<string, unknown>;

const toNum = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

/** A number field that may arrive as `{value, provenance_id}`, a number or a decimal string. */
function toPV(v: unknown, fallbackPid: string | null): PV | null {
  if (v && typeof v === "object" && "value" in v) {
    const o = v as { value: unknown; provenance_id?: unknown };
    const value = toNum(o.value);
    if (value === null) return null;
    return { value, provenance_id: typeof o.provenance_id === "string" ? o.provenance_id : (fallbackPid ?? "") };
  }
  const value = toNum(v);
  return value === null ? null : { value, provenance_id: fallbackPid ?? "" };
}

/** A quantity / cost that may arrive as a decimal string, a number or a PV → decimal string. */
function toDecimalText(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "string") return v;
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : null;
  if (typeof v === "object" && "value" in v) return toDecimalText((v as { value: unknown }).value);
  return null;
}

function toSymbol(v: unknown, currency: string): SymbolInfo {
  if (v && typeof v === "object" && "canonical" in v) return v as SymbolInfo;
  const text = typeof v === "string" ? v : "";
  return { raw: text, canonical: text, asset_type: "stock", market: "", currency, display: text.replace(/\.BK$/, "") };
}

const toErrors = (v: unknown): EntryError[] => (Array.isArray(v) ? (v as EntryError[]) : []);

/** Normalizes the preview response so components only ever see `PreviewRow` (PV for numbers). */
export function normalizePreview(raw: unknown): PositionsPreview {
  const body = (raw ?? {}) as Loose;
  const rows = Array.isArray(body.rows) ? (body.rows as Loose[]) : [];
  return {
    errors: toErrors(body.errors),
    rows: rows.map((r, i) => {
      const pid = typeof r.provenance_id === "string" ? r.provenance_id : null;
      // per-field provenance ids (backend `provenance: {price: "prov_…", …}`) win over the row id
      const prov = (r.provenance && typeof r.provenance === "object" ? r.provenance : {}) as Record<string, unknown>;
      const pidOf = (field: string): string | null => (typeof prov[field] === "string" ? (prov[field] as string) : pid);
      const currency = typeof r.currency === "string" ? r.currency : "";
      const errors = toErrors(r.errors);
      const status = r.status === "error" || r.status === "ok" ? r.status : errors.length ? "error" : "ok";
      return {
        index: typeof r.index === "number" ? r.index : i,
        symbol: toSymbol(r.symbol, currency),
        mode: (typeof r.mode === "string" ? r.mode : "qty_cost") as EntryMode,
        status,
        quantity: toDecimalText(r.quantity ?? r.units),
        avg_cost: toDecimalText(r.avg_cost),
        price: toPV(r.price, pidOf("price")),
        price_as_of: typeof r.price_as_of === "string" ? r.price_as_of : null,
        currency,
        market_value: toPV(r.market_value, pidOf("market_value")),
        cost_basis: toPV(r.cost_basis, pidOf("cost_basis")),
        unrealized_pnl: toPV(r.unrealized_pnl, pidOf("unrealized_pnl")),
        unrealized_pnl_pct: toPV(r.unrealized_pnl_pct, pidOf("unrealized_pnl_pct")),
        provenance_id: pid,
        errors,
      };
    }),
  };
}

/** A 503 `llm_not_configured` from `parse-holdings` (any of the error body shapes FastAPI may use). */
export function isLlmNotConfigured(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.type === "llm_not_configured") return true;
  const body = (err.body ?? {}) as { error?: unknown; detail?: unknown };
  const tag = (v: unknown): string | null =>
    typeof v === "string" ? v : v && typeof v === "object" && "type" in v ? String((v as { type: unknown }).type) : null;
  const detail = body.detail && typeof body.detail === "object" ? (body.detail as { error?: unknown }).error : body.detail;
  return tag(body.error) === "llm_not_configured" || tag(detail) === "llm_not_configured" || err.status === 503;
}

/** The per-row errors of a 422 from PUT / import commit, or null for any other failure. */
export function entryErrors(err: unknown): EntryError[] | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const body = err.body as { errors?: EntryError[] } | null;
  return Array.isArray(body?.errors) ? body.errors : null;
}

const pf = (id: string) => `/portfolios/${encodeURIComponent(id)}`;

async function postCsv<T>(path: string, csv: string): Promise<T> {
  return request<T>(path, { method: "POST", body: csv, headers: { "content-type": "text/csv" } });
}

export const portfolioApi = {
  list: () => request<{ portfolios: PortfolioBrief[] }>("/portfolios"),
  get: (id: string) => request<PortfolioOut>(pf(id)),
  create: (body: PortfolioWrite) => request<PortfolioOut>("/portfolios", json(body)),
  put: (id: string, body: PortfolioWrite) => request<PortfolioOut>(pf(id), json(body, "PUT")),
  patch: (id: string, body: { name?: string; icon?: string | null; color?: string | null }) =>
    request<PortfolioOut>(pf(id), json(body, "PATCH")),
  remove: (id: string) => request<{ deleted: string }>(pf(id), { method: "DELETE" }),
  analytics: (id: string, fresh = false) =>
    request<PortfolioAnalytics>(`${pf(id)}/analytics${query({ fresh: fresh ? "true" : undefined })}`),
  summary: (opts: { currency?: string; market?: MarketFilter; alt_currency?: string }) =>
    request<PortfolioSummary>(`/portfolios/summary${query(opts)}`),
  importPreview: (id: string, csv: string) => postCsv<ImportPreview>(`${pf(id)}/import`, csv),
  importCommit: (id: string, csv: string) =>
    postCsv<ImportPreview & { portfolio: PortfolioOut }>(`${pf(id)}/import?commit=1`, csv),
  advise: (id: string, body: AdviseRequest) =>
    request<{ run_id: string; thread_id: string }>(`${pf(id)}/advise`, json(body)),
  advisorRuns: (id: string) => request<{ runs: AdvisorRun[] }>(`${pf(id)}/advisor-runs`),
  plans: (id: string) => request<{ plans: PlanRecord[] }>(`${pf(id)}/plans`),
  applyPlan: (id: string, planId: string, opts: { dryRun?: boolean; force?: boolean } = {}) =>
    request<{ dry_run: boolean; diff: ApplyDiff; revision_id?: string; portfolio?: PortfolioOut }>(
      `${pf(id)}/plans/${encodeURIComponent(planId)}/apply${query({
        dry_run: opts.dryRun ? "true" : undefined,
        force: opts.force ? "true" : undefined,
      })}`,
      { method: "POST" },
    ),
  /** (P16) resolve entry modes + latest price without saving; `id` null = the default book */
  positionsPreview: async (id: string | null, positions: PositionInput[]) =>
    normalizePreview(
      await request<unknown>(id ? `${pf(id)}/positions/preview` : "/portfolio/positions/preview", json({ positions })),
    ),
  /** (P16) broker-app text → rows (LLM, no prices, no side effect); 503 when no LLM is configured */
  parseHoldings: (body: ParseHoldingsRequest) => request<ParsedHoldings>("/portfolios/parse-holdings", json(body)),
  quote: (symbol: string) => request<QuoteOut>(`/market/${encodeURIComponent(symbol)}/quote`),
  revisions: (id: string) => request<{ revisions: RevisionRecord[] }>(`${pf(id)}/revisions`),
  reports: (portfolioId?: string) =>
    request<WeeklyReports>(`/portfolios/reports${query({ portfolio_id: portfolioId })}`),
  runReports: (portfolioIds?: string[]) =>
    request<{ started: Record<string, unknown>[] }>(
      "/portfolios/reports/run",
      json(portfolioIds ? { portfolio_ids: portfolioIds } : {}),
    ),
};

export { API_BASE };
