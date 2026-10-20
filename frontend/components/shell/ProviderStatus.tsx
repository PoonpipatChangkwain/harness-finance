"use client";

/** Provider dots from /config/status (ui-spec §3): green = configured, grey = no key (tooltip names the env). */

import { useEffect, useState } from "react";

import { StateDot } from "@/components/ui/StateDot";
import { api, type ConfigStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";

const ENV_HINT: Record<string, string> = {
  anthropic: "ANTHROPIC_API_KEY",
  google: "GOOGLE_API_KEY",
  openai: "OPENAI_API_KEY",
  xai: "XAI_API_KEY",
  thaillm: "THAILLM_API_KEY",
  ollama: "OLLAMA_BASE_URL",
  ollama_cloud: "OLLAMA_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  groq: "GROQ_API_KEY",
  openrouter: "OPENROUTER_API_KEY",
  fred: "FRED_API_KEY",
  alpha_vantage: "ALPHA_VANTAGE_API_KEY",
  finnhub: "FINNHUB_API_KEY",
  fmp: "FMP_API_KEY",
  financial_datasets: "FINANCIAL_DATASETS_API_KEY",
  x_api: "X_BEARER_TOKEN",
  adanos: "ADANOS_API_KEY",
  exa: "EXASEARCH_API_KEY",
  perplexity: "PERPLEXITY_API_KEY",
  tavily: "TAVILY_API_KEY",
  langsearch: "LANGSEARCH_API_KEY",
  reddit_api: "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET",
  sec_api: "SEC_API_KEY",
};

// the full and the compact sidebar are both mounted (CSS picks one): share one request
let pending: Promise<ConfigStatus> | null = null;
function loadStatus(): Promise<ConfigStatus> {
  pending ??= api.configStatus().catch((err: unknown) => {
    pending = null;
    throw err;
  });
  return pending;
}

export function ProviderStatus({ compact }: { compact?: boolean }) {
  const t = useT();
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    loadStatus()
      .then(setStatus)
      .catch(() => setError(true));
  }, []);

  if (error) return <div className="px-2 text-[11px] text-error">{t("config/status ไม่ตอบ", "config/status not responding")}</div>;
  if (!status) return null;
  const llm = "error" in status.llm ? null : status.llm;
  const llmProviders = llm ? Object.entries(llm.providers) : [];
  const data = Object.entries(status.data);

  const dot = (id: string, label: string, configured: boolean) => (
    <span
      key={id}
      title={
        configured
          ? `${label}: configured`
          : t(
              `${label}: ไม่มี key — ใส่ ${ENV_HINT[id] ?? id.toUpperCase()} ใน backend/.env`,
              `${label}: no key — set ${ENV_HINT[id] ?? id.toUpperCase()} in backend/.env`,
            )
      }
      className="inline-flex items-center gap-1"
    >
      <StateDot state={configured ? "done" : "idle"} label={`${label} ${configured ? "configured" : "missing"}`} />
      {compact ? null : <span className="text-[11px] text-muted">{label}</span>}
    </span>
  );

  return (
    <div data-testid="provider-status" className="space-y-1.5 px-2 text-[11px]">
      {llm ? (
        <div className="truncate text-subtle" title={`deep ${llm.deep.provider}:${llm.deep.model} · quick ${llm.quick.provider}:${llm.quick.model}`}>
          {compact ? "LLM" : `LLM · ${llm.quick.provider}:${llm.quick.model}`}
        </div>
      ) : (
        <div className="text-error" title={"error" in status.llm ? status.llm.error : ""}>LLM config error</div>
      )}
      <div className="flex flex-wrap gap-x-2 gap-y-1">
        {llmProviders.filter(([, p]) => !compact || p.configured).map(([id, p]) => dot(id, id === "ollama_cloud" ? "Ollama Cloud" : (p.label.split(" ")[0] ?? id), p.configured))}
      </div>
      {compact ? null : (
        <>
          <div className="text-subtle">Data</div>
          <div className="flex flex-wrap gap-x-2 gap-y-1">{data.map(([id, p]) => dot(id, id, p.configured))}</div>
        </>
      )}
    </div>
  );
}
