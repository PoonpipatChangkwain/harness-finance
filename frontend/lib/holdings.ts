/**
 * Draft rows of the holdings editor (phase-13 §13.2, phase-16 §16.3) ↔ the backend `PositionInput` list.
 *
 * Each row is entered in one `EntryMode` (contracts §5, `domain/portfolio/entry.py`):
 * - `qty_cost`         quantity + optional avg cost
 * - `qty_pnl_pct`      quantity + % gain/loss          → backend computes avg cost from the price
 * - `qty_pnl_amount`   quantity + gain/loss in money   → backend computes avg cost from the price
 * - `value_pnl_pct`    market value + % gain/loss      → backend computes units + avg cost
 * - `value_pnl_amount` market value + gain/loss money  → backend computes units + avg cost
 * - `cost_total`       quantity + total cost           → backend computes avg cost
 * - `weight`           declared % only (the whole book must then be %-only)
 * Percent text ↔ fraction is the only conversion done here; values, weights and P&L shown
 * in the UI always come from the backend (F1).
 */

import type { EntryError, EntryMode, PortfolioData, PositionInput } from "./portfolio-api";
import { tr } from "./i18n";
import { fractionToPercentText, numberText, percentTextToFraction } from "./portfolio-format";

export type { EntryMode } from "./portfolio-api";
/** @deprecated kept for older imports — an entry kind is an `EntryMode`. */
export type EntryKind = EntryMode;

/** The modes the guided dialog / mode menu offer (weight has its own %-only switch). */
export const ENTRY_MODES = [
  "qty_cost",
  "qty_pnl_pct",
  "qty_pnl_amount",
  "value_pnl_pct",
  "value_pnl_amount",
  "cost_total",
] as const satisfies readonly EntryMode[];
export type GuidedMode = (typeof ENTRY_MODES)[number];

export const isGuidedMode = (m: unknown): m is GuidedMode => (ENTRY_MODES as readonly unknown[]).includes(m);

/** Draft fields the user types into. */
export type DraftField = "quantity" | "value" | "avgCost" | "pnlPct" | "pnlAmount" | "costTotal";

export interface ModeSpec {
  /** quantity or market value */
  first: { field: "quantity" | "value"; api: "quantity" | "value"; required: true };
  second: { field: Exclude<DraftField, "quantity" | "value">; api: "avg_cost" | "pnl_pct" | "pnl_amount" | "cost_total"; required: boolean };
  /** the second field is a gain/loss (sign matters) */
  signed: boolean;
}

export const MODE_SPEC: Record<GuidedMode, ModeSpec> = {
  qty_cost: {
    first: { field: "quantity", api: "quantity", required: true },
    second: { field: "avgCost", api: "avg_cost", required: false },
    signed: false,
  },
  qty_pnl_pct: {
    first: { field: "quantity", api: "quantity", required: true },
    second: { field: "pnlPct", api: "pnl_pct", required: true },
    signed: true,
  },
  qty_pnl_amount: {
    first: { field: "quantity", api: "quantity", required: true },
    second: { field: "pnlAmount", api: "pnl_amount", required: true },
    signed: true,
  },
  value_pnl_pct: {
    first: { field: "value", api: "value", required: true },
    second: { field: "pnlPct", api: "pnl_pct", required: false },
    signed: true,
  },
  value_pnl_amount: {
    first: { field: "value", api: "value", required: true },
    second: { field: "pnlAmount", api: "pnl_amount", required: true },
    signed: true,
  },
  cost_total: {
    first: { field: "quantity", api: "quantity", required: true },
    second: { field: "costTotal", api: "cost_total", required: true },
    signed: false,
  },
};

/** "THB" → "บาท" (Thai UI), anything else → its code ("USD"). */
export const moneyUnit = (currency: string | null | undefined): string =>
  !currency || currency === "THB" ? tr("บาท", "THB") : currency;

/** Label of a mode as the broker apps show it; money labels follow the symbol's currency. */
export function modeLabel(mode: EntryMode, currency?: string | null): string {
  const unit = moneyUnit(currency);
  switch (mode) {
    case "qty_cost":
      return tr("จำนวน + ต้นทุนเฉลี่ย", "Quantity + avg cost");
    case "qty_pnl_pct":
      return tr("จำนวน + % กำไร/ขาดทุน", "Quantity + % P/L");
    case "qty_pnl_amount":
      return tr(`จำนวน + กำไร/ขาดทุน (${unit})`, `Quantity + P/L (${unit})`);
    case "value_pnl_pct":
      return tr("มูลค่า + % กำไร/ขาดทุน", "Value + % P/L");
    case "value_pnl_amount":
      return tr(`มูลค่า + กำไร/ขาดทุน (${unit})`, `Value + P/L (${unit})`);
    case "cost_total":
      return tr("จำนวน + ต้นทุนรวม", "Quantity + total cost");
    default:
      return tr("% น้ำหนัก", "% weight");
  }
}

/** Label of one input (dialog / table placeholders). */
export function fieldLabel(field: DraftField, currency?: string | null): string {
  const unit = moneyUnit(currency);
  switch (field) {
    case "quantity":
      return tr("จำนวนหุ้น", "Shares");
    case "value":
      return tr(`มูลค่า (${unit})`, `Value (${unit})`);
    case "avgCost":
      return tr(`ต้นทุนเฉลี่ย (${unit})`, `Avg cost (${unit})`);
    case "pnlPct":
      return tr("% กำไร/ขาดทุน", "% P/L");
    case "pnlAmount":
      return tr(`กำไร/ขาดทุน (${unit})`, `P/L (${unit})`);
    default:
      return tr(`ต้นทุนรวม (${unit})`, `Total cost (${unit})`);
  }
}

/** Backend `EntryError.field` → the draft field it belongs to. */
export const API_FIELD: Record<string, DraftField> = {
  quantity: "quantity",
  value: "value",
  avg_cost: "avgCost",
  pnl_pct: "pnlPct",
  pnl_amount: "pnlAmount",
  cost_total: "costTotal",
};

export interface DraftRow {
  key: string;
  symbol: string;
  kind: EntryMode;
  quantity: string;
  avgCost: string;
  value: string;
  pnlPct: string;
  pnlAmount: string;
  costTotal: string;
  weightPct: string;
  targetPct: string;
  note: string;
  isNew: boolean;
}

let seq = 0;
export const newKey = () => `row-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function emptyRow(kind: EntryMode): DraftRow {
  return {
    key: newKey(),
    symbol: "",
    kind,
    quantity: "",
    avgCost: "",
    value: "",
    pnlPct: "",
    pnlAmount: "",
    costTotal: "",
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

/** Saved positions → draft rows (saved rows are `qty_cost`, or `weight` for a declared %). */
export function rowsFromBook(book: PortfolioData | null | undefined): DraftRow[] {
  return (book?.positions ?? []).map((p) => {
    const declared = p.declared_weight !== null && Number(p.quantity) === 0;
    return {
      ...emptyRow(declared ? "weight" : "qty_cost"),
      key: `saved-${p.symbol.canonical}`,
      symbol: p.symbol.canonical,
      quantity: declared ? "" : p.quantity,
      avgCost: p.avg_cost ?? "",
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
    return { ...r, kind: r.quantity ? "qty_cost" : "value_pnl_pct" };
  });
}

/** A parsed / typed `PositionInput` → a draft row (mode given, or inferred from the fields present). */
export function draftFromPosition(p: PositionInput): DraftRow {
  const has = (v: string | undefined) => v !== undefined && v !== null && v !== "";
  let kind: EntryMode;
  if (p.mode) kind = p.mode;
  else if (has(p.weight) && !has(p.quantity) && !has(p.value)) kind = "weight";
  else if (has(p.quantity)) {
    kind = has(p.avg_cost)
      ? "qty_cost"
      : has(p.cost_total)
        ? "cost_total"
        : has(p.pnl_amount)
          ? "qty_pnl_amount"
          : has(p.pnl_pct)
            ? "qty_pnl_pct"
            : "qty_cost";
  } else kind = has(p.pnl_amount) ? "value_pnl_amount" : "value_pnl_pct";
  return {
    ...emptyRow(kind),
    symbol: p.symbol ?? "",
    quantity: p.quantity ?? "",
    avgCost: p.avg_cost ?? "",
    value: p.value ?? "",
    pnlPct: fractionToPercentText(p.pnl_pct),
    pnlAmount: p.pnl_amount ?? "",
    costTotal: p.cost_total ?? "",
    weightPct: fractionToPercentText(p.weight),
    targetPct: fractionToPercentText(p.target_weight),
    note: p.note ?? "",
  };
}

function put<K extends keyof PositionInput>(out: PositionInput, key: K, value: PositionInput[K] | undefined) {
  if (value !== undefined && value !== "") out[key] = value;
}

/** Draft rows → the `positions` of `PUT /portfolios/{id}` (same order, so 422 indexes map back). Always sends `mode`. */
export function buildPositions(rows: DraftRow[]): PositionInput[] {
  return rows.map((r) => {
    const out: PositionInput = { symbol: r.symbol.trim(), mode: r.kind };
    if (r.kind === "weight") {
      put(out, "weight", percentTextToFraction(r.weightPct));
    } else {
      const spec = MODE_SPEC[r.kind];
      put(out, spec.first.api, numberText(r[spec.first.field]));
      const second = r[spec.second.field];
      put(out, spec.second.api, spec.second.field === "pnlPct" ? percentTextToFraction(second) : numberText(second));
    }
    put(out, "target_weight", percentTextToFraction(r.targetPct));
    put(out, "note", r.note.trim() || undefined);
    return out;
  });
}

const isNum = (t: string) => numberText(t) !== undefined;
const asNum = (t: string) => Number(numberText(t));
const pctNum = (t: string) => Number(t.trim().replace(/%$/, "").replace(/,/g, ""));

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
    if (!sym) return err("symbol", tr("เลือก symbol", "Pick a symbol"));
    if (seen.has(sym)) err("symbol", tr(`ซ้ำกับแถว ${seen.get(sym)! + 1}`, `Duplicate of row ${seen.get(sym)! + 1}`));
    seen.set(sym, index);
    if (r.kind === "weight") {
      const n = pctNum(r.weightPct);
      if (!r.weightPct.trim() || !Number.isFinite(n) || n <= 0 || n > 100) err("weight", tr("% น้ำหนัก 0–100", "% weight 0–100"));
    } else {
      const spec = MODE_SPEC[r.kind];
      if (spec.first.field === "quantity") {
        if (!isNum(r.quantity)) err("quantity", tr("กรอกจำนวนหุ้น", "Enter the number of shares"));
        else if (asNum(r.quantity) === 0) err("quantity", tr("จำนวนต้องไม่เป็น 0", "Quantity must not be 0"));
      } else if (!isNum(r.value) || asNum(r.value) <= 0) err("value", tr("มูลค่าต้องมากกว่า 0", "Value must be greater than 0"));

      const text = r[spec.second.field];
      const field = spec.second.api;
      if (!text.trim()) {
        if (spec.second.required)
          err(
            field,
            field === "pnl_amount"
              ? tr("กรอกกำไร/ขาดทุน", "Enter the P/L")
              : field === "pnl_pct"
                ? tr("กรอก % กำไร/ขาดทุน", "Enter the % P/L")
                : tr("กรอกต้นทุนรวม", "Enter the total cost"),
          );
      } else if (field === "pnl_pct") {
        if (!Number.isFinite(pctNum(text)) || pctNum(text) <= -100) err(field, tr("% กำไร/ขาดทุนต้องมากกว่า -100", "% P/L must be greater than -100"));
      } else if (!isNum(text)) {
        err(field, tr("ต้องเป็นตัวเลข", "Must be a number"));
      } else if (field !== "pnl_amount" && asNum(text) < 0) {
        err(
          field,
          field === "avg_cost"
            ? tr("ต้นทุนต้องเป็นตัวเลข ≥ 0", "Cost must be a number ≥ 0")
            : tr("ต้นทุนรวมต้องเป็นตัวเลข ≥ 0", "Total cost must be a number ≥ 0"),
        );
      }
    }
    if (r.targetPct.trim()) {
      const t = pctNum(r.targetPct);
      if (!Number.isFinite(t) || t < 0 || t > 100) err("target_weight", "target 0–100%");
    }
  });
  if (rows.some((r) => r.kind === "weight") && declaredTotalPct(rows) > 100 + 1e-9) {
    errors.push({ index: null, symbol: null, field: "weight", message: tr(`% รวม ${declaredTotalPct(rows).toFixed(2)}% เกิน 100%`, `Total ${declaredTotalPct(rows).toFixed(2)}% exceeds 100%`) });
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

/**
 * Add new rows to the book's positions: a symbol already in the book is replaced in place
 * (its target % and note are kept unless the new row sets them), others are appended.
 * `indexOf[i]` = where added row `i` landed, so a 422 of the PUT maps back to the new rows.
 */
export function mergePositions(existing: PositionInput[], added: PositionInput[]): { positions: PositionInput[]; indexOf: number[] } {
  const positions = [...existing];
  const replaced = new Set<number>();
  const indexOf = added.map((p) => {
    const key = p.symbol.trim().toUpperCase();
    const at = existing.findIndex((q, i) => !replaced.has(i) && q.symbol.trim().toUpperCase() === key);
    if (at < 0) return positions.push(p) - 1;
    replaced.add(at);
    const old = existing[at]!;
    positions[at] = {
      ...(old.target_weight ? { target_weight: old.target_weight } : {}),
      ...(old.note ? { note: old.note } : {}),
      ...p,
    };
    return at;
  });
  return { positions, indexOf };
}

/** 422 of the merged PUT → errors indexed by the added rows; errors of untouched rows become book-level. */
export function remapErrors(errors: EntryError[], indexOf: number[]): EntryError[] {
  return errors.map((e) => {
    if (e.index === null || e.index === undefined) return e;
    const at = indexOf.indexOf(e.index);
    if (at >= 0) return { ...e, index: at };
    const row = `${e.index + 1}${e.symbol ? ` (${e.symbol})` : ""}`;
    return { ...e, index: null, message: tr(`แถว ${row} ในพอร์ต: ${e.message}`, `Row ${row} in the portfolio: ${e.message}`) };
  });
}
