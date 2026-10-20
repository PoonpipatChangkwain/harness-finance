/**
 * (P17) Long-term user memory + ReasoningBank endpoints (phase-17 §17.1 API table,
 * contracts.md §13). Every request carries `X-User-Id` (single-user: `usr_default`).
 * Types come from the backend OpenAPI (`pnpm gen:api` → lib/api-types.ts).
 */

import { json, query, request } from "./api";
import type { components } from "./api-types";
import { localized } from "./i18n";

type Schemas = components["schemas"];

export const USER_ID = "usr_default";
const USER = { headers: { "X-User-Id": USER_ID } } as const;

const withUser = (init: RequestInit = {}): RequestInit => ({ ...init, headers: { ...USER.headers, ...(init.headers ?? {}) } });

export type MemoryItemOut = Schemas["MemoryItemOut"];
export type MemoryCategory = MemoryItemOut["category"];
export type MemorySource = MemoryItemOut["source"];
export type MemoryStatus = MemoryItemOut["status"];

/** Category names in the UI language; key order = display order on /memory (phase-17 §17.3). */
const CATEGORY_NAMES: Record<MemoryCategory, string> = localized({
  investment_style: ["สไตล์การลงทุน", "Investment style"],
  risk_tolerance: ["การรับความเสี่ยง", "Risk tolerance"],
  risk_capacity: ["ความสามารถรับความเสี่ยง", "Risk capacity"],
  goal: ["เป้าหมาย", "Goals"],
  constraint: ["ข้อจำกัด", "Constraints"],
  preference: ["ความชอบ", "Preferences"],
  life_fact: ["ข้อมูลชีวิต", "Life facts"],
  behavioral_pattern: ["พฤติกรรม", "Behavior"],
  knowledge_level: ["ระดับความรู้", "Knowledge level"],
});

/** Display order of the categories on /memory; `label` follows the UI language. */
export const MEMORY_CATEGORIES: { readonly id: MemoryCategory; readonly label: string }[] = (
  Object.keys(CATEGORY_NAMES) as MemoryCategory[]
).map((id) => ({
  id,
  get label() {
    return CATEGORY_NAMES[id];
  },
}));

export const CATEGORY_LABEL: Record<string, string> = CATEGORY_NAMES;

export const SOURCE_LABEL: Record<MemorySource, string> = localized({
  user_stated: ["คุณบอกเอง", "You said"],
  user_confirmed: ["ยืนยันแล้ว", "Confirmed"],
  inferred_behavior: ["AI อนุมาน", "AI inferred"],
  questionnaire: ["แบบสอบถาม", "Questionnaire"],
  portfolio_data: ["จากข้อมูลพอร์ต", "From portfolio data"],
  manual: ["กรอกเอง", "Entered manually"],
});

export const STATUS_LABEL: Record<MemoryStatus, string> = localized({
  candidate: ["รอยืนยัน", "Pending"],
  active: ["ใช้อยู่", "Active"],
  superseded: ["ถูกแทนที่", "Superseded"],
  retired: ["เลิกใช้", "Retired"],
  rejected: ["ปฏิเสธ", "Rejected"],
});

export type MemoryCounts = Schemas["MemoryCounts"];
export type MemoryListOut = Schemas["MemoryList"];
export type MemoryProfileOut = Schemas["MemoryProfileOut"];
export type MemoryChangeOut = Schemas["MemoryChangeOut"];
export type MemoryOpName = MemoryChangeOut["op"];
export type MemoryCreate = Schemas["MemoryCreate"];

export type ReasoningKind = Schemas["ReasoningItem"]["kind"];
export type ReasoningWorkflow = "research" | "portfolio" | "trade";

/** contracts §13.1 `reasoning_bank.tags` (OpenAPI types it as a free dict) */
export interface ReasoningTags {
  workflow: ReasoningWorkflow | (string & {});
  scenario?: string | null;
  asset_class?: string | null;
  market?: string | null;
  regime?: string | null;
  investor_style?: string | null;
  bias?: string | null;
  horizon?: string | null;
}

export type ReasoningItemOut = Omit<Schemas["ReasoningItem"], "tags"> & { tags: ReasoningTags };
export type ReasoningListOut = Omit<Schemas["ReasoningList"], "items"> & { items: ReasoningItemOut[] };
export type ReasoningUse = Schemas["UseOut"];
export type ReasoningDetailOut = { item: ReasoningItemOut; uses: ReasoningUse[] };
export type ReasoningStats = Schemas["ReasoningStats"];

export interface ReasoningFilters {
  workflow?: string;
  kind?: string;
  status?: string;
  q?: string;
  limit?: number;
}

const mem = (id: string) => `/memory/${encodeURIComponent(id)}`;
const rb = (id: string) => `/reasoning-bank/${encodeURIComponent(id)}`;

export const memoryApi = {
  /** `status=all` = every status (incl. superseded / retired / rejected) */
  list: (opts: { status?: "active" | "candidate" | "all"; category?: string } = {}) =>
    request<MemoryListOut>(`/memory${query(opts)}`, withUser()),
  profile: () => request<MemoryProfileOut>("/memory/profile", withUser()),
  create: (body: MemoryCreate) => request<Schemas["MemoryChangesOut"]>("/memory", withUser(json(body))),
  patch: (id: string, body: Schemas["MemoryPatch"]) =>
    request<MemoryItemOut>(mem(id), withUser(json(body, "PATCH"))),
  confirm: (id: string) => request<MemoryItemOut>(`${mem(id)}/confirm`, withUser({ method: "POST" })),
  reject: (id: string) => request<MemoryItemOut>(`${mem(id)}/reject`, withUser({ method: "POST" })),
  retire: (id: string) => request<MemoryItemOut>(`${mem(id)}/retire`, withUser({ method: "POST" })),
  /** forget for real (the row, and with `cascade` the whole supersede chain) */
  forget: (id: string, cascade = true) =>
    request<Schemas["MemoryDeleted"]>(`${mem(id)}${query({ cascade: cascade ? 1 : undefined })}`, withUser({ method: "DELETE" })),
  /** the supersede chain, oldest → newest */
  history: (id: string) => request<Schemas["MemoryHistory"]>(`${mem(id)}/history`, withUser()),

  reasoning: (filters: ReasoningFilters = {}) =>
    request<ReasoningListOut>(`/reasoning-bank${query({ ...filters })}`, withUser()),
  reasoningItem: (id: string) => request<ReasoningDetailOut>(rb(id), withUser()),
  retireReasoning: (id: string) => request<ReasoningItemOut>(`${rb(id)}/retire`, withUser({ method: "POST" })),
  reasoningStats: () => request<ReasoningStats>("/reasoning-bank/stats", withUser()),
};

/** First content line of the profile text (headings skipped), for one-line summaries (AdvisorPanel). */
export function profileSummary(profile: MemoryProfileOut | null): string | null {
  if (!profile) return null;
  const lines = profile.profile_text.split("\n").map((l) => l.trim()).filter(Boolean);
  const line = lines.find((l) => !l.startsWith("#")) ?? lines[0];
  return line ? line.replace(/^[#\-*•\s]+/, "").trim() || null : null;
}
