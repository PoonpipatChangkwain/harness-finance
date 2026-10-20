"use client";

/**
 * Backtest setup (P15 §UI 1): symbols, dates, the trading-run settings, the portfolio
 * (capital/risk/costs) and the model. "ประเมิน" asks the backend how many cells, how many are
 * already decided and what the rest may cost; the start button unlocks only after that.
 */

import { Calculator, Play, X } from "lucide-react";
import { useState } from "react";

import { KeyWarning, ModelPicker, choiceProblem, useLlmProviders } from "@/components/conversation/ModelPicker";
import { SymbolSearch } from "@/components/trade/SymbolSearch";
import type { LlmChoice } from "@/lib/api";
import { DEFAULT_CADENCE, defaultForm, formProblems, isoDaysAgo, toConfig, type BacktestForm } from "@/lib/backtest";
import { backtestApi, type BacktestEstimate } from "@/lib/backtest-api";
import { formatCost } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { ALL_ANALYSTS, ANALYST_LABEL } from "@/lib/trade";

import { SimFields } from "./SimFields";

const sel = "rounded-md border border-line bg-bg px-1.5 py-1 text-[13px] text-fg";

export function BacktestSetup({ onStarted }: { onStarted: (id: string) => void }) {
  const t = useT();
  const [form, setForm] = useState<BacktestForm>(() => defaultForm());
  const [llm, setLlm] = useState<LlmChoice | null>(null);
  const [est, setEst] = useState<BacktestEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const providers = useLlmProviders();
  const llmProblem = choiceProblem(llm, providers.data?.providers);

  const update = (patch: Partial<BacktestForm>) => {
    setForm((f) => ({ ...f, ...patch }));
    setEst(null); // any change invalidates the estimate
  };
  const problems = formProblems(form);
  const blocking = problems.filter((p) => !p.startsWith("horizon"));

  const estimate = async () => {
    setBusy(true);
    setError(null);
    try {
      setEst(await backtestApi.estimate(toConfig(form, llm)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const { backtest_id } = await backtestApi.start(toConfig(form, llm));
      onStarted(backtest_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const over = est !== null && est.cells > est.limit;
  return (
    <section data-testid="backtest-setup" className="space-y-4 rounded-md border border-line bg-layer-1 p-4">
      <header>
        <h1 className="text-[16px] font-semibold">{t("Backtest ใหม่", "New backtest")}</h1>
        <p className="text-[12.5px] text-muted">
          {t(
            "ให้ระบบเทรดตัดสินใจย้อนหลังทีละวัน (เห็นข้อมูลถึงวันนั้นเท่านั้น) แล้วจำลองพอร์ตด้วยเงินต้นที่กำหนด",
            "Let the trading system decide day by day in the past (seeing data only up to that day), then simulate a portfolio with your starting capital",
          )}
        </p>
      </header>

      <div className="space-y-1.5">
        <div className="text-[12px] text-muted">{t("Symbols (สูงสุด 10)", "Symbols (up to 10)")}</div>
        <div className="flex flex-wrap items-center gap-1.5">
          {form.symbols.map((s) => (
            <span key={s} data-testid="symbol-chip" className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-0.5 text-[12.5px]">
              {s}
              <button type="button" aria-label={t(`เอา ${s} ออก`, `Remove ${s}`)} onClick={() => update({ symbols: form.symbols.filter((x) => x !== s) })}>
                <X className="size-3" aria-hidden />
              </button>
            </span>
          ))}
        </div>
        {form.symbols.length < 10 ? (
          <div className="max-w-md">
            <SymbolSearch
              onPick={(o) => {
                if (!form.symbols.includes(o.symbol.canonical)) update({ symbols: [...form.symbols, o.symbol.canonical] });
              }}
            />
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-end gap-3 text-[12px] text-muted">
        <label className="flex flex-col gap-0.5">
          {t("เริ่ม", "Start")}
          <input type="date" data-testid="bt-start" max={isoDaysAgo(1)} value={form.start} onChange={(e) => update({ start: e.target.value })} className={`num ${sel}`} />
        </label>
        <label className="flex flex-col gap-0.5">
          {t("สิ้นสุด", "End")}
          <input type="date" data-testid="bt-end" max={isoDaysAgo(1)} value={form.end} onChange={(e) => update({ end: e.target.value })} className={`num ${sel}`} />
        </label>
        <label className="flex flex-col gap-0.5">
          Horizon
          <select value={form.horizon} onChange={(e) => update({ horizon: e.target.value as BacktestForm["horizon"] })} className={sel}>
            <option value="intraday">{t("intraday · แท่ง 15m", "intraday · 15m bars")}</option>
            <option value="swing">{t("swing · แท่ง 1H", "swing · 1H bars")}</option>
            <option value="position">{t("position · แท่ง 1D", "position · 1D bars")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5" title={t("ตัดสินใจทุกกี่วันทำการ", "Decide every how many trading days")}>
          {t("ตัดสินใจทุก (วันทำการ)", "Decide every (trading days)")}
          <input
            inputMode="numeric"
            placeholder={`auto (${DEFAULT_CADENCE[form.horizon]})`}
            value={form.cadence}
            onChange={(e) => update({ cadence: e.target.value })}
            className={`num w-28 ${sel}`}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          Debate / Risk
          <span className="flex gap-1">
            {(["debateRounds", "riskRounds"] as const).map((k) => (
              <select key={k} aria-label={k} value={form[k]} onChange={(e) => update({ [k]: Number(e.target.value) })} className={sel}>
                {[1, 2, 3].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            ))}
          </span>
        </label>
        <label className="flex flex-col gap-0.5">
          {t("ภาษารายงาน", "Report language")}
          <select value={form.language} onChange={(e) => update({ language: e.target.value as "th" | "en" })} className={sel}>
            <option value="th">ไทย</option>
            <option value="en">English</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5" title={t("รัน trading run พร้อมกันกี่ตัว", "How many trading runs to run at once")}>
          {t("พร้อมกัน", "Concurrency")}
          <select value={form.concurrency} onChange={(e) => update({ concurrency: Number(e.target.value) })} className={sel}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5" title={t("หยุดเริ่ม run ใหม่เมื่อค่า LLM ถึงเพดาน", "Stop starting new runs once the LLM cost hits this cap")}>
          {t("งบ LLM (USD)", "LLM budget (USD)")}
          <input inputMode="decimal" placeholder={t("ไม่จำกัด", "unlimited")} value={form.budgetUsd} onChange={(e) => update({ budgetUsd: e.target.value })} className={`num w-24 ${sel}`} />
        </label>
      </div>

      <fieldset className="flex flex-wrap items-center gap-3 text-[12.5px]">
        <legend className="mb-1 text-[12px] text-muted">
          {t("Analysts (ข่าว/โซเชียลย้อนหลังมักไม่มีข้อมูล — แนะนำ Market)", "Analysts (historical news/social data is often missing — Market is recommended)")}
        </legend>
        {ALL_ANALYSTS.map((a) => (
          <label key={a} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={form.analysts.includes(a)}
              onChange={() => update({ analysts: form.analysts.includes(a) ? form.analysts.filter((x) => x !== a) : [...form.analysts, a] })}
            />
            {ANALYST_LABEL[a]}
          </label>
        ))}
        <label className="ml-auto flex items-center gap-1">
          <input type="checkbox" checked={form.baseline} onChange={(e) => update({ baseline: e.target.checked })} />
          {t("เทียบกับกลยุทธ์ SMA20/50 (ไม่ใช้ LLM)", "Compare with an SMA20/50 strategy (no LLM)")}
        </label>
      </fieldset>

      <div className="space-y-1.5 rounded-md border border-line bg-bg p-3">
        <div className="text-[12.5px] font-medium">{t("พอร์ตจำลอง", "Simulated portfolio")}</div>
        <SimFields value={form.sim} onChange={(sim) => update({ sim })} />
        <p className="text-[11.5px] text-subtle">
          {t("เปลี่ยนเงินต้น/ความเสี่ยงหลังรันเสร็จได้ทันทีโดยไม่ต้องเรียก LLM ซ้ำ", "You can change capital/risk after the run finishes without calling the LLM again")}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <ModelPicker providers={providers.data} choice={llm} onChange={(c) => { setLlm(c); setEst(null); }} disabled={busy} />
        <button
          type="button"
          data-testid="bt-estimate"
          disabled={busy || blocking.length > 0 || llmProblem !== null}
          onClick={() => void estimate()}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-line px-3 py-1 text-[13px] hover:bg-layer-2 disabled:opacity-50"
        >
          <Calculator className="size-3.5" aria-hidden /> {t("ประเมิน", "Estimate")}
        </button>
        <button
          type="button"
          data-testid="bt-start-run"
          disabled={busy || est === null || over || blocking.length > 0}
          onClick={() => void start()}
          className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[13px] font-medium text-bg disabled:opacity-50"
        >
          <Play className="size-3.5" aria-hidden /> {t("เริ่ม backtest", "Start backtest")}
        </button>
      </div>
      {llmProblem ? <KeyWarning message={llmProblem} /> : null}

      {est ? (
        <div data-testid="bt-estimate-result" className="rounded-md border border-line bg-bg px-3 py-2 text-[12.5px]">
          <div>
            {t("ประมาณ", "About")} <b className="num">{est.cells}</b> trading run ({form.symbols.length} symbol) ·{" "}
            {t("ใช้ผลเดิมได้", "reusable")} <b className="num">{est.cached}</b> · {t("ต้องรันใหม่", "to run")}{" "}
            <b className="num">{est.to_run}</b>
          </div>
          {est.est_cost_usd !== null ? (
            <div className="text-muted">
              {t("ค่า LLM", "LLM cost")} ≈ <b className="num text-fg">{formatCost(est.est_cost_usd)}</b>
              {est.est_llm_calls !== null ? ` · ≈${Math.round(est.est_llm_calls).toLocaleString()} LLM calls` : ""}
              {est.est_minutes !== null ? ` · ≈${Math.ceil(est.est_minutes)} ${t("นาที", "min")}` : ""}{" "}
              {t(`(เฉลี่ยจาก ${est.based_on_runs} trade run ล่าสุด)`, `(averaged over the last ${est.based_on_runs} trade runs)`)}
            </div>
          ) : (
            <div className="text-warning">
              {t(
                "ยังประเมินค่าใช้จ่ายไม่ได้ — ยังไม่เคยรัน trade run ด้วยโมเดลนี้ (ลองรันที่หน้า Trade สัก 1 ครั้งก่อน)",
                "Cannot estimate the cost yet — no trade run with this model so far (run one on the Trade page first)",
              )}
            </div>
          )}
          {over ? <div className="text-error">
              {t(`เกินเพดาน ${est.limit} run — ย่นช่วงวันหรือเพิ่มระยะตัดสินใจ`, `Over the ${est.limit}-run cap — shorten the date range or decide less often`)}
            </div> : null}
        </div>
      ) : null}
      {problems.length ? (
        <ul className="list-disc pl-5 text-[12px] text-warning">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p role="alert" className="text-[12.5px] text-error">
          {error}
        </p>
      ) : null}
    </section>
  );
}
