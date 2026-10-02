/**
 * Typed client for the backend HTTP API (contracts.md §11).
 * Request/response shapes come from lib/api-types.ts (`pnpm gen:api`); the
 * few endpoints that return untyped dicts (runs, provenance, scratchpad,
 * config status) are typed here by hand after their backend models.
 */

import type { components } from "./api-types";

type Schemas = components["schemas"];
export type SessionOut = Schemas["SessionOut"];
export type SessionListItem = Schemas["SessionListItem"];
export type SessionDetail = Schemas["SessionDetail"];
export type MessageOut = Schemas["MessageOut"];
export type RunBrief = Schemas["RunBrief"];
export type SkillInfo = Schemas["SkillInfo"];
export type MessageAccepted = Schemas["MessageAccepted"];
export type PostMessage = Schemas["PostMessage"];

/** backend app/domain/models/provenance.py ProvenanceRecord (a type alias: table rows must be records) */
export type ProvenanceRecord = {
  id: string;
  kind: "data" | "compute" | "llm_assumption" | "user_input";
  value: unknown;
  unit: string | null;
  source: string;
  tool: string | null;
  calculation: string | null;
  params: Record<string, unknown>;
  inputs: string[];
  agent: string | null;
  run_id: string | null;
  as_of: string | null;
  fetched_at: string;
  url: string | null;
};

export interface LineageNode {
  record: ProvenanceRecord;
  inputs: LineageNode[];
  missing_inputs: string[];
}

/** One line of the run's scratchpad JSONL (backend app/harness/state/scratchpad.py). */
export interface ScratchpadEntry {
  type: string;
  ts: string;
  tool?: string;
  [key: string]: unknown;
}

export interface ScratchpadPage {
  total: number;
  offset: number;
  entries: ScratchpadEntry[];
}

export interface RunRecord {
  id: string;
  session_id: string | null;
  workflow: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  usage: Record<string, unknown>;
  input: Record<string, unknown>;
  error?: string | null;
}

export interface LlmTierStatus {
  provider: string;
  model: string;
  configured: boolean;
}

export interface ConfigStatus {
  data: Record<string, { configured: boolean; tier: string; capabilities: string[] }>;
  llm:
    | {
        deep: LlmTierStatus;
        quick: LlmTierStatus;
        failover: { deep: LlmTierStatus[]; quick: LlmTierStatus[] };
        providers: Record<string, { label: string; configured: boolean }>;
      }
    | { error: string };
}

export interface LlmProviderInfo {
  id: string;
  label: string;
  configured: boolean;
  env_hint: string;
  free: boolean;
  recommended: { quick: string | null; deep: string | null };
  models: string[];
}

export interface LlmProviders {
  default: { deep: LlmTierStatus; quick: LlmTierStatus } | null;
  providers: LlmProviderInfo[];
}

export interface LlmChoice {
  provider: string;
  model: string;
}

export interface SymbolInfo {
  raw: string;
  canonical: string;
  asset_type: string;
  market: string;
  currency: string;
  display: string;
}

export interface SymbolMatch {
  symbol: SymbolInfo;
  name: string;
  exchange: string | null;
  score: number;
}

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly type: string,
    message: string,
    /** parsed JSON error body (e.g. the per-row `errors` of a 422) */
    readonly body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    throw new ApiError(0, "network", err instanceof Error ? err.message : String(err));
  }
  if (!res.ok) {
    let type = "http_error";
    let message = `${res.status} ${res.statusText}`;
    let parsed: unknown = null;
    try {
      const body = (await res.json()) as { error?: { type?: string; message?: string }; detail?: unknown };
      parsed = body;
      if (body.error) {
        type = body.error.type ?? type;
        message = body.error.message ?? message;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      } else if (Array.isArray(body.detail)) {
        message = body.detail.map((d) => (typeof d === "string" ? d : ((d as { msg?: string }).msg ?? JSON.stringify(d)))).join("; ");
      }
    } catch {
      // non-JSON error body: keep the status line
    }
    throw new ApiError(res.status, type, message, parsed);
  }
  return (await res.json()) as T;
}

export const json = (body: unknown, method = "POST"): RequestInit => ({ method, body: JSON.stringify(body) });

export function query(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  listSessions: (kind: "research" | "portfolio" = "research") =>
    request<Schemas["SessionList"]>(`/sessions${query({ kind })}`),
  getSession: (id: string) => request<SessionDetail>(`/sessions/${id}`),
  createSession: (body: Schemas["CreateSession"]) => request<SessionOut>("/sessions", json(body)),
  deleteSession: (id: string) => request<Schemas["SessionDeleted"]>(`/sessions/${id}`, { method: "DELETE" }),
  postMessage: (sessionId: string, body: PostMessage) =>
    request<MessageAccepted>(`/sessions/${sessionId}/messages`, json(body)),
  getRun: (id: string) =>
    request<{
      run: RunRecord;
      active: boolean;
      current_node: string | null;
      pending_approval: Record<string, unknown> | null;
      resumable: boolean;
    }>(`/runs/${id}`),
  cancelRun: (id: string) => request<{ run_id: string; cancelled: boolean }>(`/runs/${id}/cancel`, { method: "POST" }),
  resumeRun: (id: string) =>
    request<{ run_id: string; resumed_from_node: string | null }>(`/runs/${id}/resume`, { method: "POST" }),
  resolveApproval: (
    id: string,
    body: { outcome: "approved" | "rejected" | "edited"; edits?: Record<string, unknown>; comment?: string },
  ) => request<{ approval: Record<string, unknown> }>(`/approvals/${id}`, json(body)),
  scratchpad: (id: string, opts: { offset?: number; limit?: number; tool?: string } = {}) =>
    request<ScratchpadPage>(`/runs/${id}/scratchpad${query(opts)}`),
  provenance: (runId: string) => request<{ records: ProvenanceRecord[] }>(`/runs/${runId}/provenance`),
  provenanceRecord: (pid: string) => request<{ record: ProvenanceRecord }>(`/provenance/${pid}`),
  lineage: (pid: string) => request<{ lineage: LineageNode }>(`/provenance/${pid}?lineage=1`),
  skills: () => request<SkillInfo[]>("/skills"),
  configStatus: () => request<ConfigStatus>("/config/status"),
  llmProviders: () => request<LlmProviders>("/llm/providers"),
  health: () => request<Record<string, unknown>>("/health"),
  searchSymbols: (q: string) =>
    request<{ query: string; normalized: SymbolInfo | null; matches: SymbolMatch[] }>(`/symbols/search${query({ q })}`),
};
