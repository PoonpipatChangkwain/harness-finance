/**
 * Draft rows of the holdings editor (phase-13 §13.2) ↔ the backend `PositionInput` list.
 *
 * Each row is entered one of three ways, matching the backend (`domain/portfolio/entry.py`):
 * - `qty`    quantity + optional avg cost
 * - `value`  market value (symbol currency) + % gain — the backend turns it into units/cost
 * - `weight` declared % only (the whole book must then be %-only)
 * Percent text ↔ fraction is the only conversion done here; values, weights and P&L shown
 * in the table always come from the analytics (F1).
 */

import type { EntryError, PortfolioData, PositionInput } from "./portfolio-api";
import { fractionToPercentText, numberText, percentTextToFraction } from "./portfolio-format";

export type EntryKind = "qty" | "value" | "weight";

export interface DraftRow {
  key: string;
  symbol: string;
  kind: EntryKind;
  quantity: string;
  avgCost: string;
  value: string;
  pnlPct: string;
  weightPct: string;
  targetPct: string;
  note: string;
  isNew: boolean;
}

let seq = 0;
export const newKey = () => `row-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function emptyRow(kind: EntryKind): DraftRow {
  return {
    key: newKey(),
    symbol: "",
    kind,
    quantity: "",
    avgCost: "",
    value: "",
    pnlPct: "",
    weightPct: "",
    targetPct: "",
    note: "",
    isNew: true,
  };
}

/** A book entered as declared % only (every position has a weight and no quantity). */
export function isPercentBook(book: PortfolioData | null | undefined): boolean {
  if (!book || book.positions.length === 0) return false;
  return book.positions.every((p) => p.declared_weight !== null && Number(p.quantity) === 0);
}

export function rowsFromBook(book: PortfolioData | null | undefined): DraftRow[] {
  return (book?.positions ?? []).map((p) => {
    const declared = p.declared_weight !== null && Number(p.quantity) === 0;
    return {
      key: `saved-${p.symbol.canonical}`,
      symbol: p.symbol.canonical,
      kind: declared ? "weight" : "qty",
      quantity: declared ? "" : p.quantity,
      avgCost: p.avg_cost ?? "",
      value: "",
      pnlPct: "",
      weightPct: fractionToPercentText(p.declared_weight),
      targetPct: fractionToPercentText(p.target_weight),
      note: p.note ?? "",
      isNew: false,
    };
  });
}

/** Switch every row to %-only (or back): symbol, target and note are kept. */
export function convertRows(rows: DraftRow[], percent: boolean): DraftRow[] {
  return rows.map((r) => {
    if (percent) return r.kind === "weight" ? r : { ...r, kind: "weight" };
    if (r.kind !== "weight") return r;
    return { ...r, kind: r.quantity ? "qty" : "value" };
  });
}

function put<K extends keyof PositionInput>(out: PositionInput, key: K, value: PositionInput[K] | undefined) {
  if (value !== undefined && value !== "") out[key] = value;
}

/** Draft rows → the `positions` of `PUT /portfolios/{id}` (same order, so 422 indexes map back). */
export function buildPositions(rows: DraftRow[]): PositionInput[] {
  return rows.map((r) => {
    const out: PositionInput = { symbol: r.symbol.trim() };
    if (r.kind === "qty") {
      put(out, "quantity", numberText(r.quantity));
      put(out, "avg_cost", numberText(r.avgCost));
    } else if (r.kind === "value") {
      put(out, "value", numberText(r.value));
      put(out, "pnl_pct", percentTextToFraction(r.pnlPct));
    } else {
      put(out, "weight", percentTextToFraction(r.weightPct));
    }
    put(out, "target_weight", percentTextToFraction(r.targetPct));
    put(out, "note", r.note.trim() || undefined);
    return out;
  });
}

const isNum = (t: string) => numberText(t) !== undefined;
const pctNum = (t: string) => Number(t.trim().replace(/%$/, ""));

/** Sum of the declared % typed so far (live validation of the %-only mode, not analytics). */
export function declaredTotalPct(rows: DraftRow[]): number {
  return rows.reduce((sum, r) => {
    const n = pctNum(r.weightPct);
    return r.kind === "weight" && Number.isFinite(n) ? sum + n : sum;
  }, 0);
}

/** Checks done before sending (the backend validates again and answers 422 per row). */
export function validateRows(rows: DraftRow[]): EntryError[] {
  const errors: EntryError[] = [];
  const seen = new Map<string, number>();
  rows.forEach((r, index) => {
    const err = (field: string, message: string) => errors.push({ index, symbol: r.symbol || null, field, message });
    const sym = r.symbol.trim().toUpperCase();
    if (!sym) return err("symbol", "เลือก symbol");
    if (seen.has(sym)) err("symbol", `ซ้ำกับแถว ${seen.get(sym)! + 1}`);
    seen.set(sym, index);
    if (r.kind === "qty") {
      if (!isNum(r.quantity)) err("quantity", "กรอกจำนวนหุ้น");
      else if (Number(numberText(r.quantity)) === 0) err("quantity", "จำนวนต้องไม่เป็น 0");
      if (r.avgCost.trim() && (!isNum(r.avgCost) || Number(numberText(r.avgCost)) < 0)) err("avg_cost", "ต้นทุนต้องเป็นตัวเลข ≥ 0");
    } else if (r.kind === "value") {
      if (!isNum(r.value) || Number(numberText(r.value)) <= 0) err("value", "มูลค่าต้องมากกว่า 0");
      if (r.pnlPct.trim() && (!Number.isFinite(pctNum(r.pnlPct)) || pctNum(r.pnlPct) <= -100)) err("pnl_pct", "% กำไรต้องมากกว่า -100");
    } else {
      const n = pctNum(r.weightPct);
      if (!r.weightPct.trim() || !Number.isFinite(n) || n <= 0 || n > 100) err("weight", "% น้ำหนัก 0–100");
    }
    if (r.targetPct.trim()) {
      const t = pctNum(r.targetPct);
      if (!Number.isFinite(t) || t < 0 || t > 100) err("target_weight", "target 0–100%");
    }
  });
  if (rows.some((r) => r.kind === "weight") && declaredTotalPct(rows) > 100 + 1e-9) {
    errors.push({ index: null, symbol: null, field: "weight", message: `% รวม ${declaredTotalPct(rows).toFixed(2)}% เกิน 100%` });
  }
  return errors;
}

/** Errors keyed by row (index → messages) plus the book-level ones. */
export function groupErrors(errors: EntryError[]): { rows: Map<number, EntryError[]>; book: EntryError[] } {
  const rows = new Map<number, EntryError[]>();
  const book: EntryError[] = [];
  for (const e of errors) {
    if (e.index === null || e.index === undefined) book.push(e);
    else rows.set(e.index, [...(rows.get(e.index) ?? []), e]);
  }
  return { rows, book };
}
