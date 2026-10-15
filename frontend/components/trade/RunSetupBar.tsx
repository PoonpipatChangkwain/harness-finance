"use client";

/**
 * Trade run setup (phase-11 §11.2): react-hook-form + zod after `TradeRunInput`.
 * Analysts follow the asset type (forex/commodity/crypto: no fundamentals, macro on),
 * the date defaults to today and a past date shows the backtest badge (data cut at as_of).
 */

import { clsx } from "clsx";
import { zodResolver } from "@hookform/resolvers/zod";
import { Play } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { KeyWarning, ModelPicker, choiceProblem, useLlmProviders } from "@/components/conversation/ModelPicker";
import type { LlmChoice, SymbolInfo } from "@/lib/api";
import { ALL_ANALYSTS, ANALYST_LABEL, allowedAnalysts, defaultAnalysts, todayIso } from "@/lib/trade";
import type { AnalystName, TradeRunRequest } from "@/lib/trade-api";

export const setupSchema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "วันที่ไม่ถูกต้อง"),
  analysts: z.array(z.enum(["market", "news", "fundamentals", "sentiment", "macro"])).min(1, "เลือกอย่างน้อย 1 analyst"),
  debateRounds: z.number().int().min(1).max(3),
  riskRounds: z.number().int().min(1).max(3),
  usePortfolio: z.boolean(),
  useMl: z.boolean(),
  mlOnly: z.boolean(),
  horizon: z.enum(["intraday", "swing", "position"]),
  language: z.enum(["th", "en"]),
});

export type SetupForm = z.infer<typeof setupSchema>;

export function defaultSetup(assetType: string | null | undefined, today = todayIso()): SetupForm {
  return {
    date: today,
    analysts: defaultAnalysts(assetType),
    debateRounds: 1,
    riskRounds: 1,
    usePortfolio: true,
    useMl: true,
    mlOnly: false,
    horizon: "swing",
    language: "th",
  };
}

export const isBacktest = (date: string, today = todayIso()) => date < today;

/** Form → `POST /trade/runs` body. Today = latest bar (no as_of); a past date = backtest. */
export function toRunRequest(symbol: SymbolInfo, form: SetupForm, llm: LlmChoice | null, today = todayIso()): TradeRunRequest {
  const allowed = allowedAnalysts(symbol.asset_type);
  return {
    symbol: symbol.canonical,
    analysis_date: isBacktest(form.date, today) ? form.date : null,
    analysts: form.analysts.filter((a) => allowed.includes(a)),
    debate_rounds: form.debateRounds,
    risk_rounds: form.riskRounds,
    use_portfolio: form.usePortfolio,
    use_ml: form.useMl,
    ml_only: form.useMl && form.mlOnly,
    use_smc: false, // original LLM desk (trader v6, no SMC) whenever no ML signal leads
    horizon: form.horizon,
    language: form.language,
    llm,
  };
}

export function RunSetupBar({
  symbol,
  busy,
  disabled,
  llm,
  onLlmChange,
  onRun,
}: {
  symbol: SymbolInfo;
  busy?: boolean;
  disabled?: boolean;
  llm: LlmChoice | null;
  onLlmChange: (choice: LlmChoice | null) => void;
  onRun: (body: TradeRunRequest) => void;
}) {
  const form = useForm<SetupForm>({ resolver: zodResolver(setupSchema), defaultValues: defaultSetup(symbol.asset_type) });
  const { register, watch, setValue, handleSubmit, formState } = form;
  const providers = useLlmProviders();
  const llmProblem = choiceProblem(llm, providers.data?.providers);

  // a new asset type resets the analysts to its defaults (forex: fundamentals off, macro on)
  useEffect(() => {
    setValue("analysts", defaultAnalysts(symbol.asset_type));
  }, [symbol.asset_type, setValue]);

  const allowed = allowedAnalysts(symbol.asset_type);
  const chosen = watch("analysts");
  const date = watch("date");
  const useMl = watch("useMl");
  const toggle = (a: AnalystName) =>
    setValue("analysts", chosen.includes(a) ? chosen.filter((x) => x !== a) : [...chosen, a], { shouldValidate: true });

  return (
    <form
      data-testid="run-setup"
      onSubmit={handleSubmit((values) => onRun(toRunRequest(symbol, values, llm)))}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-line bg-layer-1 px-3 py-2 text-[12.5px]"
    >
      <label className="flex items-center gap-1.5 text-muted">
        วันที่
        <input type="date" max={todayIso()} {...register("date")} className="num rounded-md border border-line bg-bg px-1.5 py-0.5 text-fg" />
      </label>
      {isBacktest(date) ? (
        <span data-testid="backtest-badge" className="rounded-sm bg-warning/15 px-1.5 py-0.5 text-[11.5px] text-warning">
          backtest mode: ข้อมูลถูกตัดตาม as_of
        </span>
      ) : null}
      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="sr-only">Analysts</legend>
        {ALL_ANALYSTS.filter((a) => allowed.includes(a)).map((a) => (
          <label key={a} className="flex items-center gap-1">
            <input type="checkbox" checked={chosen.includes(a)} onChange={() => toggle(a)} data-testid={`analyst-${a}`} />
            {ANALYST_LABEL[a]}
          </label>
        ))}
      </fieldset>
      <label className="flex items-center gap-1 text-muted">
        Debate
        <select {...register("debateRounds", { valueAsNumber: true })} className="rounded-md border border-line bg-bg px-1 text-fg">
          {[1, 2, 3].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1 text-muted">
        Risk
        <select {...register("riskRounds", { valueAsNumber: true })} className="rounded-md border border-line bg-bg px-1 text-fg">
          {[1, 2, 3].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1 text-muted">
        Horizon
        <select {...register("horizon")} className="rounded-md border border-line bg-bg px-1 text-fg">
          <option value="intraday">intraday · แท่ง 15m · ถือไม่กี่ชม.</option>
          <option value="swing">swing · แท่ง 1H · 1–5 วัน</option>
          <option value="position">position · แท่ง 1D · สัปดาห์+</option>
        </select>
      </label>
      <label className="flex items-center gap-1 text-muted">
        ภาษา
        <select {...register("language")} className="rounded-md border border-line bg-bg px-1 text-fg">
          <option value="th">ไทย</option>
          <option value="en">English</option>
        </select>
      </label>
      <label className="flex items-center gap-1">
        <input type="checkbox" {...register("usePortfolio")} /> ใช้พอร์ตของฉัน
      </label>
      <label className="flex items-center gap-1" title="ใช้ได้เฉพาะ symbol/horizon ที่มีโมเดลที่ validate แล้ว (ตอนนี้ XAUUSD intraday/swing)">
        <input type="checkbox" data-testid="use-ml" {...register("useMl")} /> ML signal
      </label>
      <label
        className={clsx("flex items-center gap-1", !useMl && "opacity-50")}
        title="ถ้า ML ออก long/short: ไม่เรียก LLM เลย (ข้าม analysts) entry/stop/TP มาจาก label ของโมเดล ถ้า flat จบเป็น no_trade"
      >
        <input type="checkbox" data-testid="ml-only" disabled={!useMl} {...register("mlOnly")} /> ML ล้วน (ไม่ใช้ LLM)
      </label>
      <ModelPicker providers={providers.data} choice={llm} onChange={onLlmChange} disabled={busy || disabled} />
      <button
        type="submit"
        data-testid="run-trade"
        disabled={busy || disabled || llmProblem !== null}
        className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[13px] font-medium text-bg disabled:opacity-50"
      >
        <Play className="size-3.5" aria-hidden /> Run
      </button>
      {formState.errors.analysts ? <p className="w-full text-error">{formState.errors.analysts.message}</p> : null}
      {llmProblem ? <KeyWarning message={llmProblem} /> : null}
    </form>
  );
}
