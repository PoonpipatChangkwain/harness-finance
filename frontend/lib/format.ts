/**
 * Display formatting only (never arithmetic on financial numbers — F1).
 * Token format after deepseek-harness ui-chat `token-format.ts`: 950, 1.2k, 3.4M.
 */

export function formatTokens(n: number): string {
  if (!Number.isFinite(n)) return "–";
  const abs = Math.abs(n);
  if (abs < 1000) return String(Math.round(n));
  if (abs < 1_000_000) return `${trim(n / 1000)}k`;
  return `${trim(n / 1_000_000)}M`;
}

function trim(v: number): string {
  const s = Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  return s.replace(/\.0$/, "");
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "–";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}

export function formatCost(usd: number): string {
  if (!usd) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(3)}`;
}

export function formatTime(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined) return "";
  const d = typeof ts === "number" ? new Date(ts) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "";
  const diff = Math.max(0, now - t) / 1000;
  if (diff < 60) return "เมื่อสักครู่";
  if (diff < 3600) return `${Math.floor(diff / 60)} นาทีที่แล้ว`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ชม.ที่แล้ว`;
  return new Date(t).toLocaleDateString("th-TH", { day: "numeric", month: "short" });
}

export function shortId(id: string, keep = 6): string {
  return id.length > keep + 5 ? `${id.slice(0, 5)}…${id.slice(-keep)}` : id;
}

/** Render a provenance / tool value compactly as text. */
export function formatValue(value: unknown, max = 120): string {
  let s: string;
  if (value === null || value === undefined) s = "–";
  else if (typeof value === "string") s = value;
  else if (typeof value === "number" || typeof value === "boolean") s = String(value);
  else s = JSON.stringify(value);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function firstLine(text: string | null | undefined): string {
  return (text ?? "").split("\n")[0]?.trim() ?? "";
}
