/**
 * `GET/PUT /api/settings` (phase-13 §13.4), typed after `app/api/routes/settings.py`.
 * The API never carries API keys — only whether each provider is configured.
 */

import { json, request } from "./api";

export interface TierValue {
  provider: string;
  model: string;
  configured: boolean;
}

export interface LlmValues {
  deep: TierValue | null;
  quick: TierValue | null;
  failover: TierValue[];
  error?: string;
}

export interface LimitsValues {
  max_iterations: number;
  max_tool_calls_total: number;
  max_calls_per_tool: number;
  similarity_threshold: number;
  tool_timeout_s: number;
  run_wall_clock_s: number;
  max_cost_usd: number;
  mc_count_trigger: number | null;
  mc_keep_recent: number | null;
  mc_token_trigger: number | null;
  autocompact_buffer_tokens: number | null;
}

export interface PolicyValues {
  max_position_weight: number | null;
  max_sector_weight: number | null;
  max_risk_per_trade: number | null;
  min_confidence: number | null;
  block_on_low_confidence: boolean;
  min_risk_reward: number | null;
  require_stop_loss: boolean;
  max_turnover: number | null;
}

export interface SettingsValues {
  llm: LlmValues;
  data: { order: Record<string, string[]> };
  limits: LimitsValues;
  policy: PolicyValues;
}

export interface LlmCatalogEntry {
  id: string;
  label: string;
  configured: boolean;
  env_hint: string;
  free: boolean;
  recommended: { quick: string | null; deep: string | null };
  models: string[];
  /** (P16) short note shown under the provider (e.g. claude_code: personal subscription only) */
  note?: string | null;
  /** (P16) why the provider is not configured (KeyDot tooltip) */
  configured_reason?: string | null;
}

export interface DataCatalogEntry {
  mode: "failover" | "fanout";
  providers: { id: string; registered: boolean; configured: boolean; tier: string }[];
}

export type SettingsSection = "llm" | "data" | "limits" | "policy";

export interface SettingsView {
  values: SettingsValues;
  defaults: SettingsValues;
  policy_builtin: Record<keyof PolicyValues, number | boolean | null>;
  overrides: Record<SettingsSection, Record<string, unknown>>;
  catalog: { llm: LlmCatalogEntry[]; data: Record<string, DataCatalogEntry> };
}

export interface TierChoice {
  provider: string;
  model: string;
}

export interface SettingsUpdate {
  llm?: { deep?: TierChoice | null; quick?: TierChoice | null; failover?: TierChoice[] | null };
  data?: { order?: Record<string, string[]> };
  limits?: Partial<Record<keyof LimitsValues, number>>;
  policy?: Partial<{ [K in keyof PolicyValues]: NonNullable<PolicyValues[K]> }>;
}

export const settingsApi = {
  get: () => request<SettingsView>("/settings"),
  put: (body: SettingsUpdate) => request<SettingsView>("/settings", json(body, "PUT")),
};
