"use client";

/**
 * Settings (ui-spec §7, phase-13 §13.4): LLM tiers + failover, data provider order per
 * capability, harness limits, policy limits, appearance. Each section saves on its own
 * (`PUT /api/settings` with that section only) and has "Reset to defaults" (an empty
 * section). API keys are never shown or sent — only whether each one is set, and which
 * env var to put it in.
 */

import { clsx } from "clsx";
import { Plus, RotateCcw, Save } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { tr, type Locale, useLocale, useLocaleStore, useT } from "@/lib/i18n";
import { formatPct, fractionToPercentText, percentTextToFraction } from "@/lib/portfolio-format";
import {
  settingsApi,
  type LimitsValues,
  type PolicyValues,
  type SettingsSection,
  type SettingsUpdate,
  type SettingsView as View,
  type TierChoice,
} from "@/lib/settings-api";
import { useUiStore, type ThemeMode } from "@/stores/ui-store";

import { OrderList } from "./OrderList";

type Api = typeof settingsApi;

function Section({
  id,
  title,
  description,
  children,
  onSave,
  onReset,
  dirty,
  busy,
  overridden,
}: {
  id: string;
  title: string;
  description?: string;
  children: ReactNode;
  onSave?: () => void;
  onReset?: () => void;
  dirty?: boolean;
  busy?: boolean;
  overridden?: boolean;
}) {
  const t = useT();
  return (
    <section id={id} data-testid={`section-${id}`} className="rounded-lg border border-line bg-layer-1">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <h2 className="text-[14px] font-semibold">{title}</h2>
        {overridden ? <span className="rounded-sm bg-accent-muted px-1.5 text-[11px] text-accent">{t("แก้จากค่าเริ่มต้น", "Changed from default")}</span> : null}
        <span className="flex-1" />
        {onReset ? (
          <button
            type="button"
            data-testid={`reset-${id}`}
            disabled={busy}
            onClick={onReset}
            className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12px] text-muted hover:bg-layer-2 disabled:opacity-50"
          >
            <RotateCcw className="size-3.5" aria-hidden /> {t("คืนค่าเริ่มต้น", "Reset to defaults")}
          </button>
        ) : null}
        {onSave ? (
          <button
            type="button"
            data-testid={`save-${id}`}
            disabled={busy || !dirty}
            onClick={onSave}
            className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-0.5 text-[12px] text-bg disabled:opacity-40"
          >
            <Save className="size-3.5" aria-hidden /> {t("บันทึก", "Save")}
          </button>
        ) : null}
      </div>
      <div className="space-y-3 p-4 text-[12.5px]">
        {description ? <p className="text-muted">{description}</p> : null}
        {children}
      </div>
    </section>
  );
}

function KeyDot({ configured, hint, reason }: { configured: boolean; hint?: string; reason?: string | null }) {
  const t = useT();
  return (
    <span
      title={
        configured
          ? t("มี key แล้ว", "Key configured")
          : reason
            ? reason
            : hint
              ? t(`ยังไม่มี key: ตั้ง ${hint} ใน backend/.env`, `No key yet: set ${hint} in backend/.env`)
              : t("ยังไม่ได้ตั้งค่า", "Not configured")
      }
      className={clsx("inline-block size-2 shrink-0 rounded-full", configured ? "bg-done" : "bg-idle")}
    />
  );
}

// -- LLM ------------------------------------------------------------------------------------

function TierPicker({ view, value, onChange, label }: { view: View; value: TierChoice; onChange: (v: TierChoice) => void; label: string }) {
  const t = useT();
  const entry = view.catalog.llm.find((p) => p.id === value.provider);
  const listId = `models-${label}`;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-14 text-muted">{label}</span>
      <select
        aria-label={`${label} provider`}
        value={value.provider}
        onChange={(e) => {
          const p = view.catalog.llm.find((x) => x.id === e.target.value);
          onChange({ provider: e.target.value, model: (label === "quick" ? p?.recommended.quick : p?.recommended.deep) ?? p?.models[0] ?? "" });
        }}
        className="rounded-md border border-line bg-bg px-2 py-1 text-fg"
      >
        {view.catalog.llm.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
            {p.configured ? "" : t(" (ไม่มี key)", " (no key)")}
          </option>
        ))}
      </select>
      <input
        aria-label={`${label} model`}
        list={listId}
        value={value.model}
        onChange={(e) => onChange({ ...value, model: e.target.value })}
        className="num min-w-56 flex-1 rounded-md border border-line bg-bg px-2 py-1 text-fg outline-none focus:border-accent"
      />
      <datalist id={listId}>
        {entry?.models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <KeyDot configured={entry?.configured ?? false} hint={entry?.env_hint} reason={entry?.configured_reason} />
      {entry && !entry.configured ? <span className="text-[11.5px] text-warning">{t(`ตั้ง ${entry.env_hint} ใน backend/.env`, `Set ${entry.env_hint} in backend/.env`)}</span> : null}
    </div>
  );
}

function LlmSection({ view, save, busy }: { view: View; save: (body: SettingsUpdate) => Promise<void>; busy: boolean }) {
  const t = useT();
  const v = view.values.llm;
  const fallback: TierChoice = { provider: view.catalog.llm[0]?.id ?? "", model: "" };
  const init = () => ({
    deep: v.deep ? { provider: v.deep.provider, model: v.deep.model } : fallback,
    quick: v.quick ? { provider: v.quick.provider, model: v.quick.model } : fallback,
    failover: v.failover.map((f) => ({ provider: f.provider, model: f.model })),
  });
  const [draft, setDraft] = useState(init);
  const [adding, setAdding] = useState<TierChoice>(fallback);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- reset when the server values change
  useEffect(() => setDraft(init()), [view]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(init());
  const configured = (id: string) => view.catalog.llm.find((p) => p.id === id)?.configured ?? false;

  return (
    <Section
      id="llm"
      title="LLM"
      description={t(
        "deep = วิเคราะห์/ตัดสินใจ, quick = งานเบา (สรุป, compaction) · ถ้า provider หลักล้มจะใช้ลำดับ failover",
        "deep = analysis/decisions, quick = light work (summaries, compaction) · if the main provider fails the failover order is used",
      )}
      dirty={dirty}
      busy={busy}
      overridden={Object.keys(view.overrides.llm).length > 0}
      onSave={() => void save({ llm: { deep: draft.deep, quick: draft.quick, failover: draft.failover } })}
      onReset={() => void save({ llm: {} })}
    >
      {v.error ? <p className="text-warning">{v.error}</p> : null}
      <TierPicker view={view} label="deep" value={draft.deep} onChange={(deep) => setDraft({ ...draft, deep })} />
      <TierPicker view={view} label="quick" value={draft.quick} onChange={(quick) => setDraft({ ...draft, quick })} />
      <div>
        <div className="mb-1 text-muted">{t("ลำดับ failover (ลากเพื่อเรียง)", "Failover order (drag to reorder)")}</div>
        {draft.failover.length ? (
          <OrderList
            label="llm failover"
            items={draft.failover}
            keyOf={(f) => `${f.provider}:${f.model}`}
            onChange={(failover) => setDraft({ ...draft, failover })}
            onRemove={(i) => setDraft({ ...draft, failover: draft.failover.filter((_, j) => j !== i) })}
            render={(f) => (
              <span className="flex items-center gap-2">
                <KeyDot configured={configured(f.provider)} />
                <span className="num">
                  {f.provider}:{f.model}
                </span>
              </span>
            )}
          />
        ) : (
          <p className="text-subtle">{t("ไม่มี failover", "No failover")}</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            aria-label="failover provider"
            value={adding.provider}
            onChange={(e) => setAdding({ provider: e.target.value, model: view.catalog.llm.find((p) => p.id === e.target.value)?.models[0] ?? "" })}
            className="rounded-md border border-line bg-bg px-2 py-1"
          >
            {view.catalog.llm.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          <input
            aria-label="failover model"
            value={adding.model}
            onChange={(e) => setAdding({ ...adding, model: e.target.value })}
            placeholder="model"
            className="num w-56 rounded-md border border-line bg-bg px-2 py-1"
          />
          <button
            type="button"
            disabled={!adding.model.trim()}
            onClick={() => setDraft({ ...draft, failover: [...draft.failover, { provider: adding.provider, model: adding.model.trim() }] })}
            className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 disabled:opacity-40"
          >
            <Plus className="size-3.5" aria-hidden /> {t("เพิ่ม", "Add")}
          </button>
        </div>
      </div>
      <div>
        <div className="mb-1 text-muted">{t("สถานะ key", "Key status")}</div>
        <ul className="grid gap-1 sm:grid-cols-2">
          {view.catalog.llm.map((p) => (
            <li key={p.id} data-testid="llm-catalog-entry" className="flex items-start gap-2">
              <span className="pt-1.5">
                <KeyDot configured={p.configured} hint={p.env_hint} reason={p.configured_reason} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  {p.label}
                  {p.free ? <span className="text-[10.5px] text-done">free</span> : null}
                  <span className="num ml-auto text-[11px] text-subtle">{p.configured ? t("มี key", "has key") : p.env_hint}</span>
                </span>
                {p.note ? (
                  <span data-testid="llm-catalog-note" className="block text-[11px] text-subtle">
                    {p.note}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

// -- data -----------------------------------------------------------------------------------

/** Only capabilities whose order differs from the default go into the override. */
export function changedOrders(draft: Record<string, string[]>, defaults: Record<string, string[]>): Record<string, string[]> {
  return Object.fromEntries(Object.entries(draft).filter(([cap, order]) => JSON.stringify(order) !== JSON.stringify(defaults[cap] ?? [])));
}

function DataSection({ view, save, busy }: { view: View; save: (body: SettingsUpdate) => Promise<void>; busy: boolean }) {
  const t = useT();
  const [draft, setDraft] = useState(view.values.data.order);
  useEffect(() => setDraft(view.values.data.order), [view]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(view.values.data.order);
  const caps = Object.keys(view.catalog.data).sort();
  return (
    <Section
      id="data"
      title="Data providers"
      description={t(
        "ลำดับ provider ต่อ capability: failover = ลองตามลำดับจนได้ข้อมูล, fanout = เรียกทุกตัวแล้วรวมผล · run ถัดไปใช้ลำดับใหม่ (ดูได้จาก provenance.source)",
        "Provider order per capability: failover = try in order until data comes back, fanout = call all and merge · the next run uses the new order (see provenance.source)",
      )}
      dirty={dirty}
      busy={busy}
      overridden={Object.keys(view.overrides.data).length > 0}
      onSave={() => void save({ data: { order: changedOrders(draft, view.defaults.data.order) } })}
      onReset={() => void save({ data: {} })}
    >
      <div className="grid gap-3 lg:grid-cols-2">
        {caps.map((cap) => {
          const entry = view.catalog.data[cap]!;
          const info = new Map(entry.providers.map((p) => [p.id, p]));
          const order = draft[cap] ?? [];
          const missing = entry.providers.filter((p) => !order.includes(p.id));
          const changed = JSON.stringify(order) !== JSON.stringify(view.defaults.data.order[cap] ?? []);
          return (
            <div key={cap} data-testid={`cap-${cap}`} className="rounded-md border border-line/70 p-2">
              <div className="mb-1 flex items-center gap-2">
                <span className="num font-medium">{cap}</span>
                <span className="rounded-sm bg-layer-2 px-1 text-[10.5px] text-subtle">{entry.mode}</span>
                {changed ? <span className="text-[10.5px] text-accent">{t("แก้แล้ว", "changed")}</span> : null}
              </div>
              <OrderList
                label={`${cap} order`}
                items={order}
                keyOf={(id) => id}
                onChange={(next) => setDraft({ ...draft, [cap]: next })}
                onRemove={order.length > 1 ? (i) => setDraft({ ...draft, [cap]: order.filter((_, j) => j !== i) }) : undefined}
                render={(id) => {
                  const p = info.get(id);
                  return (
                    <span className="flex items-center gap-2">
                      <KeyDot configured={p?.configured ?? false} />
                      <span className="num">{id}</span>
                      {p ? <span className="text-[10.5px] text-subtle">tier {p.tier}</span> : null}
                      {p && !p.registered ? <span className="text-[10.5px] text-subtle">{t("(ยังไม่มี)", "(not available)")}</span> : null}
                    </span>
                  );
                }}
              />
              {missing.length ? (
                <div className="mt-1 flex flex-wrap gap-1">
                  {missing.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setDraft({ ...draft, [cap]: [...order, p.id] })}
                      className="num rounded-sm border border-dashed border-line px-1.5 text-[11px] text-subtle hover:text-fg"
                    >
                      + {p.id}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </Section>
  );
}

// -- limits / policy ------------------------------------------------------------------------

type LimitKey = keyof LimitsValues;
/** Dexter / deepseek-harness reference values shown next to each field. */
export const DEXTER_DEFAULTS: Partial<Record<LimitKey, number>> = {
  max_iterations: 10,
  max_calls_per_tool: 3,
  similarity_threshold: 0.7,
  mc_count_trigger: 8,
  mc_keep_recent: 4,
  mc_token_trigger: 80_000,
  autocompact_buffer_tokens: 13_000,
};
/** `label` = [Thai, English] */
const LIMIT_FIELDS: { key: LimitKey; label: readonly [string, string]; step?: string }[] = [
  { key: "max_iterations", label: ["Max iterations ต่อ query", "Max iterations per query"] },
  { key: "max_tool_calls_total", label: ["Tool call limit ต่อ run", "Tool call limit per run"] },
  { key: "max_calls_per_tool", label: ["เรียก tool เดิมได้สูงสุด (ครั้ง)", "Max calls of the same tool"] },
  { key: "similarity_threshold", label: ["Loop detection: similarity ≥", "Loop detection: similarity ≥"], step: "0.05" },
  { key: "tool_timeout_s", label: ["Tool timeout (วินาที)", "Tool timeout (seconds)"] },
  { key: "run_wall_clock_s", label: ["Wall clock ต่อ run (วินาที)", "Wall clock per run (seconds)"] },
  { key: "max_cost_usd", label: ["Budget ต่อ run ($)", "Budget per run ($)"], step: "0.1" },
  { key: "mc_count_trigger", label: ["Microcompact เมื่อ tool result เกิน (ชิ้น)", "Microcompact when tool results exceed (items)"] },
  { key: "mc_keep_recent", label: ["Microcompact เก็บล่าสุด (ชิ้น)", "Microcompact keeps the latest (items)"] },
  { key: "mc_token_trigger", label: ["Microcompact เมื่อรวมเกิน (tokens)", "Microcompact when the total exceeds (tokens)"] },
  { key: "autocompact_buffer_tokens", label: ["Full compact buffer (tokens)", "Full compact buffer (tokens)"] },
];

/** Typed fields → the section body: only fields that differ from the default. */
export function changedNumbers<K extends string>(
  draft: Record<K, string>,
  defaults: Record<K, number | null>,
): Partial<Record<K, number>> {
  const out: Partial<Record<K, number>> = {};
  for (const [k, text] of Object.entries(draft) as [K, string][]) {
    if (!text.trim()) continue;
    const n = Number(text);
    if (Number.isFinite(n) && n !== defaults[k]) out[k] = n;
  }
  return out;
}

function LimitsSection({ view, save, busy }: { view: View; save: (body: SettingsUpdate) => Promise<void>; busy: boolean }) {
  const t = useT();
  const toText = (vals: LimitsValues) =>
    Object.fromEntries(LIMIT_FIELDS.map((f) => [f.key, vals[f.key] === null ? "" : String(vals[f.key])])) as Record<LimitKey, string>;
  const [draft, setDraft] = useState(() => toText(view.values.limits));
  useEffect(() => setDraft(toText(view.values.limits)), [view]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(toText(view.values.limits));
  return (
    <Section
      id="limits"
      title="Harness limits"
      description={t("ใช้กับ run ถัดไป · ช่องว่าง = ค่าเริ่มต้น", "Applies to the next run · empty = default")}
      dirty={dirty}
      busy={busy}
      overridden={Object.keys(view.overrides.limits).length > 0}
      onSave={() => void save({ limits: changedNumbers(draft, view.defaults.limits) })}
      onReset={() => void save({ limits: {} })}
    >
      <div className="grid gap-x-6 gap-y-2 md:grid-cols-2">
        {LIMIT_FIELDS.map((f) => (
          <label key={f.key} className="flex items-center gap-2">
            <span className="min-w-0 flex-1 text-muted">{t(...f.label)}</span>
            <input
              aria-label={f.key}
              type="number"
              step={f.step ?? "1"}
              value={draft[f.key]}
              placeholder={view.defaults.limits[f.key] === null ? "default" : String(view.defaults.limits[f.key])}
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
              className="num w-28 rounded-md border border-line bg-bg px-2 py-0.5 text-right"
            />
            <span className="num w-24 text-[11px] text-subtle" title={t("ค่าอ้างอิงของ Dexter", "Dexter's reference value")}>
              {DEXTER_DEFAULTS[f.key] !== undefined ? `Dexter ${DEXTER_DEFAULTS[f.key]!.toLocaleString("en-US")}` : ""}
            </span>
          </label>
        ))}
      </div>
    </Section>
  );
}

type PctKey = Exclude<keyof PolicyValues, "require_stop_loss" | "block_on_low_confidence" | "min_risk_reward">;
/** `label` = [Thai, English] */
const POLICY_FIELDS: { key: PctKey; label: readonly [string, string] }[] = [
  { key: "max_position_weight", label: ["น้ำหนักสูงสุดต่อตัว", "Max weight per position"] },
  { key: "max_sector_weight", label: ["น้ำหนักสูงสุดต่อ sector", "Max weight per sector"] },
  { key: "max_risk_per_trade", label: ["ความเสี่ยงสูงสุดต่อเทรด", "Max risk per trade"] },
  { key: "min_confidence", label: ["Confidence ขั้นต่ำ", "Min confidence"] },
  { key: "max_turnover", label: ["Turnover สูงสุดต่อการ rebalance", "Max turnover per rebalance"] },
];

export type PolicyDraft = Record<PctKey | "min_risk_reward", string>;
export interface PolicyFlags {
  require_stop_loss: boolean;
  block_on_low_confidence: boolean;
}

/** Policy form (percent text; R:R as a plain ratio) → the section body in fractions; empty = built-in default. */
export function policyBody(draft: PolicyDraft, flags: PolicyFlags): SettingsUpdate["policy"] {
  const out: NonNullable<SettingsUpdate["policy"]> = {};
  for (const f of POLICY_FIELDS) {
    const frac = percentTextToFraction(draft[f.key]);
    if (frac !== undefined) out[f.key] = Number(frac);
  }
  const rr = draft.min_risk_reward.trim();
  if (rr !== "" && Number.isFinite(Number(rr)) && Number(rr) >= 0) out.min_risk_reward = Number(rr);
  if (!flags.require_stop_loss) out.require_stop_loss = false;
  if (!flags.block_on_low_confidence) out.block_on_low_confidence = false;
  return out;
}

function PolicySection({ view, save, busy }: { view: View; save: (body: SettingsUpdate) => Promise<void>; busy: boolean }) {
  const t = useT();
  const o = view.overrides.policy as Partial<PolicyValues>;
  const toText = (): PolicyDraft => ({
    ...(Object.fromEntries(POLICY_FIELDS.map((f) => [f.key, o[f.key] !== undefined && o[f.key] !== null ? fractionToPercentText(o[f.key] as number) : ""])) as Record<PctKey, string>),
    min_risk_reward: typeof o.min_risk_reward === "number" ? String(o.min_risk_reward) : "",
  });
  const toFlags = (): PolicyFlags => ({
    require_stop_loss: view.values.policy.require_stop_loss,
    block_on_low_confidence: view.values.policy.block_on_low_confidence,
  });
  const [draft, setDraft] = useState(toText);
  const [flags, setFlags] = useState(toFlags);
  useEffect(() => {
    setDraft(toText());
    setFlags(toFlags());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset when the server values change
  }, [view]);
  const dirty = JSON.stringify(policyBody(draft, flags)) !== JSON.stringify(policyBody(toText(), toFlags()));
  const rrBuiltin = view.policy_builtin.min_risk_reward;
  return (
    <Section
      id="policy"
      title="Policy"
      description={t(
        "กฎ deterministic ของ gate (trading + portfolio advisor) · ช่องว่าง = ค่าในตัว (advisor ใช้ตาม risk profile) · ข้อจำกัดที่ใส่ตอนสั่ง run ชนะค่าตรงนี้",
        "Deterministic gate rules (trading + portfolio advisor) · empty = built-in value (the advisor follows the risk profile) · limits given when starting a run override these",
      )}
      dirty={dirty}
      busy={busy}
      overridden={Object.keys(view.overrides.policy).length > 0}
      onSave={() => void save({ policy: policyBody(draft, flags) })}
      onReset={() => void save({ policy: {} })}
    >
      <div className="grid gap-x-6 gap-y-2 md:grid-cols-2">
        {POLICY_FIELDS.map((f) => {
          const builtin = view.policy_builtin[f.key];
          return (
            <label key={f.key} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 text-muted">{t(...f.label)}</span>
              <input
                aria-label={f.key}
                value={draft[f.key]}
                inputMode="decimal"
                placeholder={typeof builtin === "number" ? fractionToPercentText(builtin) : "profile"}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                className="num w-20 rounded-md border border-line bg-bg px-2 py-0.5 text-right"
              />
              <span className="w-28 text-[11px] text-subtle">
                % · {t("ค่าในตัว", "built-in")} {typeof builtin === "number" ? formatPct(builtin, 0) : t("ตาม risk profile", "per risk profile")}
              </span>
            </label>
          );
        })}
        <label className="flex items-center gap-2">
          <span className="min-w-0 flex-1 text-muted">{t("R:R ขั้นต่ำที่ TP1 (ต้องมากกว่า · 0 = ปิด)", "Min R:R at TP1 (must exceed · 0 = off)")}</span>
          <input
            aria-label="min_risk_reward"
            value={draft.min_risk_reward}
            inputMode="decimal"
            placeholder={typeof rrBuiltin === "number" ? String(rrBuiltin) : ""}
            onChange={(e) => setDraft({ ...draft, min_risk_reward: e.target.value })}
            className="num w-20 rounded-md border border-line bg-bg px-2 py-0.5 text-right"
          />
          <span className="w-28 text-[11px] text-subtle">
            {t("เท่า · ค่าในตัว", "× · built-in")} {typeof rrBuiltin === "number" ? rrBuiltin : "-"}
          </span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            aria-label="block_on_low_confidence"
            checked={flags.block_on_low_confidence}
            onChange={(e) => setFlags({ ...flags, block_on_low_confidence: e.target.checked })}
          />
          <span className="text-muted">{t("Confidence ต่ำกว่าขั้นต่ำ = ไม่เทรด (ปิด = แค่เตือน)", "Confidence below the minimum = no trade (off = warn only)")}</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            aria-label="require_stop_loss"
            checked={flags.require_stop_loss}
            onChange={(e) => setFlags({ ...flags, require_stop_loss: e.target.checked })}
          />
          <span className="text-muted">{t("เทรดต้องมี stop-loss", "Trades must have a stop-loss")}</span>
        </label>
      </div>
    </Section>
  );
}

// -- appearance -----------------------------------------------------------------------------

const SCALES = [0.9, 1, 1.1, 1.2];

function AppearanceSection() {
  const t = useT();
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const scale = useUiStore((s) => s.fontScale);
  const setScale = useUiStore((s) => s.setFontScale);
  const locale = useLocale();
  const setLocale = useLocaleStore((s) => s.setLocale);
  return (
    <Section
      id="appearance"
      title="Appearance"
      description={t("เก็บในเบราว์เซอร์นี้", "Stored in this browser")}
      onReset={() => {
        setTheme("system");
        setScale(1);
      }}
    >
      <div className="flex flex-wrap items-center gap-4">
        <div role="radiogroup" aria-label="theme" className="flex rounded-md bg-layer-2 p-0.5">
          {(["system", "light", "dark"] as ThemeMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={theme === mode}
              onClick={() => setTheme(mode)}
              className={clsx("rounded-sm px-3 py-0.5 capitalize", theme === mode ? "bg-layer-1 text-fg" : "text-muted")}
            >
              {mode}
            </button>
          ))}
        </div>
        <div role="radiogroup" aria-label="language" data-testid="language-picker" className="flex rounded-md bg-layer-2 p-0.5">
          {(
            [
              ["th", "ไทย"],
              ["en", "English"],
            ] as [Locale, string][]
          ).map(([id, name]) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={locale === id}
              onClick={() => setLocale(id)}
              className={clsx("rounded-sm px-3 py-0.5", locale === id ? "bg-layer-1 text-fg" : "text-muted")}
            >
              {name}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-muted">
          {t("ขนาดตัวอักษร", "Font size")}
          <select aria-label="font scale" value={scale} onChange={(e) => setScale(Number(e.target.value))} className="rounded-md border border-line bg-bg px-2 py-0.5 text-fg">
            {SCALES.map((s) => (
              <option key={s} value={s}>
                {Math.round(s * 100)}%
              </option>
            ))}
          </select>
        </label>
      </div>
    </Section>
  );
}

// -- page -----------------------------------------------------------------------------------

const NAV: { id: SettingsSection | "appearance"; label: string }[] = [
  { id: "llm", label: "LLM" },
  { id: "data", label: "Data" },
  { id: "limits", label: "Limits" },
  { id: "policy", label: "Policy" },
  { id: "appearance", label: "Appearance" },
];

export function SettingsView({ api = settingsApi }: { api?: Api }) {
  const t = useT();
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get()
      .then(setView)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [api]);

  const save = useMemo(
    () => async (body: SettingsUpdate) => {
      setBusy(true);
      setError(null);
      try {
        setView(await api.put(body));
        setNotice(tr("บันทึกแล้ว · มีผลกับ run ถัดไป", "Saved · applies to the next run"));
        window.setTimeout(() => setNotice(null), 2500);
      } catch (e) {
        setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [api],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 pr-12">
        <h1 className="text-[15px] font-semibold">Settings</h1>
        <nav className="flex flex-wrap gap-1 text-[12.5px]">
          {NAV.map((n) => (
            <a key={n.id} href={`#${n.id}`} className="rounded-md px-2 py-0.5 text-muted hover:bg-layer-2 hover:text-fg">
              {n.label}
            </a>
          ))}
        </nav>
        {notice ? <span className="text-[12px] text-done">{notice}</span> : null}
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {error ? <p data-testid="settings-error" className="rounded-md bg-error/10 px-3 py-1.5 text-[12.5px] text-error">{error}</p> : null}
        <p className="text-[12px] text-subtle">
          {t("API key ตั้งได้ที่ backend/.env เท่านั้น — หน้านี้แสดงแค่ว่ามี key หรือไม่", "API keys can only be set in backend/.env — this page only shows whether a key exists")}
        </p>
        {view ? (
          <>
            <LlmSection view={view} save={save} busy={busy} />
            <DataSection view={view} save={save} busy={busy} />
            <LimitsSection view={view} save={save} busy={busy} />
            <PolicySection view={view} save={save} busy={busy} />
          </>
        ) : !error ? (
          <div className="h-40 animate-pulse rounded-lg bg-layer-1" />
        ) : null}
        <AppearanceSection />
      </div>
    </div>
  );
}
