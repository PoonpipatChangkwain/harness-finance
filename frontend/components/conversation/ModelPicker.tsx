"use client";

/**
 * Model picker in the composer: provider + model for the next message.
 * "Default" = the backend's configured chain (with failover); a picked model
 * runs alone (no failover) so a key can be tested. A provider without a key
 * shows which env var to set; the list refreshes when the window regains
 * focus, so a key added to backend/.env shows up without a restart.
 */

import { Bot, KeyRound } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api, type LlmChoice, type LlmProviderInfo, type LlmProviders } from "@/lib/api";
import { tr, useT } from "@/lib/i18n";

const CUSTOM = "__custom__";

export function useLlmProviders(): { data: LlmProviders | null; error: boolean; refresh: () => void } {
  const [data, setData] = useState<LlmProviders | null>(null);
  const [error, setError] = useState(false);
  const refresh = useCallback(() => {
    api
      .llmProviders()
      .then((d) => {
        setData(d);
        setError(false);
      })
      .catch(() => setError(true));
  }, []);
  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);
  return { data, error, refresh };
}

/** Why the chosen model cannot be used yet (null = OK to send). */
export function choiceProblem(choice: LlmChoice | null, providers: LlmProviderInfo[] | undefined): string | null {
  if (!choice || !providers) return null;
  const info = providers.find((p) => p.id === choice.provider);
  if (!info) return tr(`ไม่รู้จัก provider ${choice.provider}`, `Unknown provider ${choice.provider}`);
  if (!info.configured)
    return tr(
      `ยังไม่ได้ใส่ API key ของ ${info.label} — ตั้ง ${info.env_hint} ใน backend/.env ก่อน`,
      `No API key for ${info.label} yet — set ${info.env_hint} in backend/.env first`,
    );
  if (!choice.model.trim()) return tr("ใส่ชื่อ model ก่อน", "Enter a model name first");
  return null;
}

export function ModelPicker({
  providers,
  choice,
  onChange,
  disabled,
}: {
  providers: LlmProviders | null;
  choice: LlmChoice | null;
  onChange: (choice: LlmChoice | null) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const list = providers?.providers ?? [];
  const info = choice ? list.find((p) => p.id === choice.provider) : undefined;
  const models = info?.models ?? [];
  // any model id that is not a suggestion ("other…" = "", or a typed / restored id) shows the text box
  const custom = choice !== null && !models.includes(choice.model);
  const def = providers?.default?.quick;
  const defaultLabel = def ? `Default · ${def.provider}:${def.model}` : "Default";

  return (
    <div className="flex min-w-0 items-center gap-1">
      <Bot className="size-4 shrink-0 text-subtle" aria-hidden />
      <select
        aria-label="LLM provider"
        data-testid="provider-picker"
        value={choice?.provider ?? ""}
        disabled={disabled}
        title={
          choice
            ? t(
                `${info?.label ?? choice.provider}: หา tool + เขียนคำตอบ (ไม่มี failover) · วางแผนใช้โมเดล quick ตาม .env`,
                `${info?.label ?? choice.provider}: tool calls + answer (no failover) · planning uses the quick model from .env`,
              )
            : t("ใช้ LLM ตามที่ตั้งใน backend/.env (มี failover)", "Use the LLMs configured in backend/.env (with failover)")
        }
        onChange={(e) => {
          const id = e.target.value;
          if (!id) return onChange(null);
          const p = list.find((x) => x.id === id);
          onChange({ provider: id, model: p?.recommended.quick ?? p?.models[0] ?? "" });
        }}
        className="max-w-40 truncate rounded-md border border-line bg-layer-2 px-2 py-1 text-[12.5px] text-fg"
      >
        <option value="">{defaultLabel}</option>
        {list.map((p) => (
          <option key={p.id} value={p.id}>
            {p.configured ? "✓ " : "✗ "}
            {p.label}
            {p.configured ? "" : t(" (ไม่มี key)", " (no key)")}
          </option>
        ))}
      </select>
      {choice ? (
        <>
          <select
            aria-label="Model"
            data-testid="model-select"
            value={custom ? CUSTOM : choice.model}
            disabled={disabled}
            onChange={(e) => {
              const v = e.target.value;
              onChange({ provider: choice.provider, model: v === CUSTOM ? "" : v });
            }}
            className="num max-w-44 truncate rounded-md border border-line bg-layer-2 px-2 py-1 text-[12px] text-fg"
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            <option value={CUSTOM}>{t("อื่นๆ (พิมพ์เอง)…", "Other (type it)…")}</option>
          </select>
          {custom ? (
            <input
              aria-label="Model id"
              data-testid="model-input"
              value={choice.model}
              disabled={disabled}
              autoFocus
              onChange={(e) => onChange({ provider: choice.provider, model: e.target.value })}
              placeholder="model id"
              className="num w-40 min-w-0 rounded-md border border-line bg-layer-2 px-2 py-1 text-[12px] text-fg"
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function KeyWarning({ message }: { message: string }) {
  return (
    <p data-testid="key-warning" role="alert" className="mt-1 flex items-center gap-1.5 text-[12.5px] text-warning">
      <KeyRound className="size-3.5 shrink-0" aria-hidden />
      {message}
    </p>
  );
}
