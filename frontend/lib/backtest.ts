/** Pure helpers of the Backtest page (P15): form ↔ request, the cell grid, chart series. */

import type { LlmChoice } from "./api";
import { localized, tr, useLocaleStore } from "./i18n";
import type { BacktestCell, BacktestConfig, EquityPoint, SimConfigBody } from "./backtest-api";
import type { AnalystName } from "./trade-api";

export type Horizon = "intraday" | "swing" | "position";

/** Sessions between decisions when the user leaves cadence on "auto" (backend DEFAULT_CADENCE). */
export const DEFAULT_CADENCE: Record<Horizon, number> = { intraday: 1, swing: 3, position: 5 };

/** How far back Yahoo serves the horizon's bars (backend YAHOO_MAX_DAYS, with the warm-up). */
export const HORIZON_MAX_DAYS: Record<Horizon, number | null> = { intraday: 55, swing: 700, position: null };

/** The money part of the form, as the user types it (percent fields in %). */
export interface SimForm {
  capital: string;
  baseCurrency: "THB" | "USD";
  riskPct: string;
  maxWeightPct: string;
  maxPositions: string;
  commissionPct: string;
  slippageBps: string;
  compounding: boolean;
  allowShortForex: boolean;
}

export interface BacktestForm {
  name: string;
  symbols: string[];
  start: string;
  end: string;
  horizon: Horizon;
  cadence: string; // "" = auto
  analysts: AnalystName[];
  debateRounds: number;
  riskRounds: number;
  language: "th" | "en";
  concurrency: number;
  budgetUsd: string; // "" = none
  baseline: boolean;
  sim: SimForm;
}

export function isoDaysAgo(days: number, now = new Date()): string {
  const d = new Date(now.getTime() - days * 86_400_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export const DEFAULT_SIM: SimForm = {
  capital: "100000",
  baseCurrency: "THB",
  riskPct: "1",
  maxWeightPct: "20",
  maxPositions: "5",
  commissionPct: "0",
  slippageBps: "5",
  compounding: true,
  allowShortForex: true,
};

export function defaultForm(now = new Date()): BacktestForm {
  return {
    name: "",
    symbols: [],
    start: isoDaysAgo(90, now),
    end: isoDaysAgo(1, now),
    horizon: "swing",
    cadence: "",
    analysts: ["market"],
    debateRounds: 1,
    riskRounds: 1,
    language: useLocaleStore.getState().locale, // reports follow the UI language by default
    concurrency: 2,
    budgetUsd: "",
    baseline: true,
    sim: { ...DEFAULT_SIM },
  };
}

/** "1.5" (%) → "0.015"; blank/garbage → undefined (the backend default applies). */
export function pctToFraction(text: string): string | undefined {
  const n = Number(text.replace(/,/g, "").trim());
  if (!text.trim() || !Number.isFinite(n)) return undefined;
  return String(Number((n / 100).toPrecision(12)));
}

function num(text: string): string | undefined {
  const n = Number(text.replace(/,/g, "").trim());
  return text.trim() && Number.isFinite(n) ? String(n) : undefined;
}

export function toSimConfig(f: SimForm): SimConfigBody {
  const out: Record<string, unknown> = {
    initial_capital: num(f.capital),
    base_currency: f.baseCurrency,
    risk_pct: pctToFraction(f.riskPct),
    max_position_weight: f.maxWeightPct.trim() ? pctToFraction(f.maxWeightPct) : null,
    max_open_positions: num(f.maxPositions) ? Number(num(f.maxPositions)) : undefined,
    commission_pct: pctToFraction(f.commissionPct),
    slippage_bps: num(f.slippageBps),
    compounding: f.compounding,
    shortable: f.allowShortForex ? ["forex", "commodity"] : [],
  };
  return Object.fromEntries(Object.entries(out).filter(([, v]) => v !== undefined)) as SimConfigBody;
}

/** A stored `SimConfig` (fractions, decimal strings) → the form's percent text. */
export function simFormFrom(config: Record<string, unknown> | null | undefined): SimForm {
  if (!config) return { ...DEFAULT_SIM };
  const pct = (v: unknown, fallback: string) => (v === null || v === undefined ? fallback : String(Number((Number(v) * 100).toPrecision(10))));
  const shortable = Array.isArray(config.shortable) ? (config.shortable as string[]) : ["forex"];
  return {
    capital: String(config.initial_capital ?? DEFAULT_SIM.capital),
    baseCurrency: config.base_currency === "USD" ? "USD" : "THB",
    riskPct: pct(config.risk_pct, DEFAULT_SIM.riskPct),
    maxWeightPct: config.max_position_weight === null ? "" : pct(config.max_position_weight, DEFAULT_SIM.maxWeightPct),
    maxPositions: String(config.max_open_positions ?? DEFAULT_SIM.maxPositions),
    commissionPct: pct(config.commission_pct, DEFAULT_SIM.commissionPct),
    slippageBps: String(config.slippage_bps ?? DEFAULT_SIM.slippageBps),
    compounding: config.compounding !== false,
    allowShortForex: shortable.includes("forex"),
  };
}

export function toConfig(f: BacktestForm, llm: LlmChoice | null): BacktestConfig {
  const cadence = Number(f.cadence);
  const budget = Number(f.budgetUsd);
  return {
    name: f.name.trim(),
    symbols: f.symbols,
    start_date: f.start,
    end_date: f.end,
    horizon: f.horizon,
    cadence: f.cadence.trim() && Number.isInteger(cadence) && cadence > 0 ? cadence : null,
    analysts: f.analysts,
    debate_rounds: f.debateRounds,
    risk_rounds: f.riskRounds,
    language: f.language,
    llm,
    concurrency: f.concurrency,
    budget_usd: f.budgetUsd.trim() && budget > 0 ? budget : null,
    baseline: f.baseline,
    use_ml: true, // backend default: the desk reads the ML signal where a validated model exists
    use_smc: false, // original LLM desk (trader v6, no SMC) whenever no ML signal leads
    sim: toSimConfig(f.sim),
  };
}

/** Problems the form can catch before the server does (shown under the form). */
export function formProblems(f: BacktestForm, today = isoDaysAgo(0)): string[] {
  const out: string[] = [];
  if (!f.symbols.length) out.push(tr("เลือก symbol อย่างน้อย 1 ตัว", "Pick at least 1 symbol"));
  if (f.start >= today) out.push(tr("วันเริ่มต้องเป็นวันในอดีต", "Start date must be in the past"));
  if (f.end < f.start) out.push(tr("วันสิ้นสุดต้องไม่ก่อนวันเริ่ม", "End date must not be before the start date"));
  if (!f.analysts.length) out.push(tr("เลือก analyst อย่างน้อย 1 ตัว", "Pick at least 1 analyst"));
  const limit = HORIZON_MAX_DAYS[f.horizon];
  const start = new Date(f.start).getTime();
  if (limit !== null && (Date.now() - start) / 86_400_000 > limit) {
    out.push(
      tr(
        `horizon ${f.horizon} ย้อนได้ราว ${limit} วัน (Yahoo) — ช่วงก่อนนั้นจะใช้แท่งรายวันแทน`,
        `horizon ${f.horizon} only goes back about ${limit} days (Yahoo) — earlier dates use daily bars instead`,
      ),
    );
  }
  const capital = Number(f.sim.capital);
  if (!(capital > 0)) out.push(tr("เงินต้นต้องมากกว่า 0", "Starting capital must be greater than 0"));
  return out;
}

// -- the cell grid ------------------------------------------------------------------------------

export type CellTone = "pending" | "running" | "long" | "short" | "flat" | "failed";

export function cellTone(c: Pick<BacktestCell, "status" | "decision">): CellTone {
  if (c.status === "pending" || c.status === "running" || c.status === "failed") return c.status;
  const side = c.decision?.side;
  return side === "long" || side === "short" ? side : "flat";
}

/** Rows = symbols, columns = analysis days (sorted); missing (symbol, day) pairs are null. */
export function cellMatrix(cells: BacktestCell[]): { symbols: string[]; days: string[]; at: (s: string, d: string) => BacktestCell | null } {
  const symbols = [...new Set(cells.map((c) => c.symbol))].sort();
  const days = [...new Set(cells.map((c) => c.day))].sort();
  const index = new Map(cells.map((c) => [`${c.symbol}|${c.day}`, c]));
  return { symbols, days, at: (s, d) => index.get(`${s}|${d}`) ?? null };
}

/** Apply a live `backtest.cell` event to the cell list (unknown ids are appended). */
export function applyCellEvent(
  cells: BacktestCell[],
  ev: { cell_id: string; symbol: string; day: string; status: BacktestCell["status"]; run_id?: string | null; side?: string | null; rating?: string | null; outcome?: string | null; error?: string | null; cached: boolean },
): BacktestCell[] {
  const decision = ev.side ? { side: ev.side as "long" | "short" | "flat", rating: ev.rating ?? null, outcome: ev.outcome ?? null } : null;
  const patch = (c: BacktestCell): BacktestCell => ({
    ...c,
    status: ev.status,
    run_id: ev.run_id ?? c.run_id,
    decision: decision ?? c.decision,
    error: ev.error ?? null,
  });
  if (cells.some((c) => c.id === ev.cell_id)) return cells.map((c) => (c.id === ev.cell_id ? patch(c) : c));
  return [
    ...cells,
    patch({ id: ev.cell_id, symbol: ev.symbol, day: ev.day, status: ev.status, run_id: null, cached_from: ev.cached ? "cache" : null, decision: null, usage: {}, error: null }),
  ];
}

// -- chart series -------------------------------------------------------------------------------

export interface CurveRow {
  day: string;
  strategy?: number;
  benchmark?: number;
  baseline?: number;
}

/** Merge the curves by day; each line is shown as % of its own start (they start together). */
export function mergeCurves(
  strategy: EquityPoint[],
  benchmark: EquityPoint[],
  baseline: EquityPoint[] = [],
  mode: "money" | "percent" = "money",
): CurveRow[] {
  const rows = new Map<string, CurveRow>();
  const put = (pts: EquityPoint[], key: "strategy" | "benchmark" | "baseline") => {
    const first = pts.length ? Number(pts[0]!.equity) : 0;
    for (const p of pts) {
      const v = Number(p.equity);
      const row = rows.get(p.day) ?? { day: p.day };
      row[key] = mode === "percent" ? (first > 0 ? (v / first - 1) * 100 : 0) : v;
      rows.set(p.day, row);
    }
  };
  put(strategy, "strategy");
  put(benchmark, "benchmark");
  put(baseline, "baseline");
  return [...rows.values()].sort((a, b) => a.day.localeCompare(b.day));
}

/** Drawdown (% below the running peak) of a curve, for the band under the equity chart. */
export function drawdown(points: EquityPoint[]): { day: string; dd: number }[] {
  let peak = -Infinity;
  return points.map((p) => {
    const v = Number(p.equity);
    peak = Math.max(peak, v);
    return { day: p.day, dd: peak > 0 ? (v / peak - 1) * 100 : 0 };
  });
}

export const SKIP_LABEL: Record<string, string> = localized({
  flat: ["ไม่มีสัญญาณ (flat / no trade)", "No signal (flat / no trade)"],
  invalid_levels: ["ระดับราคาไม่ถูกต้อง", "Invalid price levels"],
  short_not_allowed: ["ชอร์ตไม่ได้ (บัญชีเงินสด)", "Shorting not allowed (cash account)"],
  already_open: ["มี position ฝั่งเดียวกันอยู่แล้ว", "A position on the same side is already open"],
  max_positions: ["ชนจำนวน position สูงสุด", "Max open positions reached"],
  below_one_lot: ["เงินไม่พอซื้อ 1 lot", "Not enough cash for 1 lot"],
  no_buying_power: ["เงินสด/มาร์จิ้นไม่พอ", "Not enough cash/margin"],
  gap_invalidated: ["ราคาเปิด gap ข้าม stop/target", "Open gapped past stop/target"],
  order_expired: ["ออเดอร์รอไม่ถูก fill", "Pending order was never filled"],
  no_bars: ["ไม่มีแท่งราคาหลังวันตัดสินใจ", "No price bars after the decision date"],
});

export const EXIT_LABEL: Record<string, string> = localized({
  tp: ["ชน target", "Target hit"],
  sl: ["ชน stop", "Stop hit"],
  time_stop: ["หมดเวลา", "Time stop"],
  reverse: ["สัญญาณกลับข้าง", "Signal reversed"],
  end_of_test: ["จบช่วงทดสอบ", "End of test"],
});
