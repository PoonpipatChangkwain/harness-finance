/**
 * Display formatting for portfolio numbers (F1: never arithmetic on money beyond the
 * fraction ↔ percent conversion of what the user types or reads).
 */

export function formatMoney(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 0.1234 → "12.34%"; `signed` adds "+" to positive values. */
export function formatPct(value: number | null | undefined, digits = 2, signed = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const s = `${(value * 100).toFixed(digits)}%`;
  return signed && value > 0 ? `+${s}` : s;
}

export function formatSigned(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const s = formatMoney(value, digits);
  return value > 0 ? `+${s}` : s;
}

export function formatQty(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

export function upDownClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return "text-fg";
  return value > 0 ? "text-up" : "text-down";
}

/** Stored fraction ("0.2") → the percent the user edits ("20"). */
export function fractionToPercentText(fraction: string | number | null | undefined): string {
  if (fraction === null || fraction === undefined || fraction === "") return "";
  const n = Number(fraction);
  if (!Number.isFinite(n)) return "";
  return String(Number((n * 100).toFixed(6)));
}

/** What the user typed as a percent ("20", "12.5%") → fraction text ("0.2"); "" → undefined. */
export function percentTextToFraction(text: string): string | undefined {
  const t = text.trim().replace(/%$/, "").replace(/,/g, "").trim();
  if (!t) return undefined;
  const n = Number(t);
  if (!Number.isFinite(n)) return undefined;
  return String(Number((n / 100).toFixed(8)));
}

/** A plain number the user typed ("1,000", " 30.5 ") → canonical text, or undefined when empty/invalid. */
export function numberText(text: string): string | undefined {
  const t = text.trim().replace(/,/g, "");
  if (!t) return undefined;
  return Number.isFinite(Number(t)) ? t : undefined;
}

export const MARKET_LABEL: Record<string, string> = { TH: "ไทย", US: "สหรัฐฯ" };
