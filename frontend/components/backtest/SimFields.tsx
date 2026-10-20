"use client";

/** The portfolio part of a backtest (P15): capital, risk, costs. Used by the setup and the what-if panel. */

import type { ReactNode } from "react";

import type { SimForm } from "@/lib/backtest";
import { useT } from "@/lib/i18n";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5 text-[12px] text-muted" title={hint}>
      {label}
      {children}
    </label>
  );
}

const input = "num w-full rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg";

export function SimFields({ value, onChange, disabled }: { value: SimForm; onChange: (v: SimForm) => void; disabled?: boolean }) {
  const t = useT();
  const set = <K extends keyof SimForm>(k: K, v: SimForm[K]) => onChange({ ...value, [k]: v });
  return (
    <div data-testid="sim-fields" className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4">
      <Field label={t("เงินต้นพอร์ต", "Starting capital")}>
        <div className="flex gap-1">
          <input
            data-testid="sim-capital"
            inputMode="decimal"
            value={value.capital}
            disabled={disabled}
            onChange={(e) => set("capital", e.target.value)}
            className={input}
          />
          <select
            aria-label={t("สกุลเงินของพอร์ต", "Portfolio currency")}
            value={value.baseCurrency}
            disabled={disabled}
            onChange={(e) => set("baseCurrency", e.target.value as SimForm["baseCurrency"])}
            className="rounded-md border border-line bg-bg px-1 text-[13px] text-fg"
          >
            <option value="THB">THB</option>
            <option value="USD">USD</option>
          </select>
        </div>
      </Field>
      <Field label={t("เสี่ยงต่อเทรด (%)", "Risk per trade (%)")} hint={t("ส่วนของเงินในพอร์ตที่ยอมเสียถ้าชน stop", "Share of the portfolio you accept to lose if the stop is hit")}>
        <input inputMode="decimal" value={value.riskPct} disabled={disabled} onChange={(e) => set("riskPct", e.target.value)} className={input} />
      </Field>
      <Field label={t("น้ำหนักสูงสุดต่อตัว (%)", "Max weight per symbol (%)")} hint={t("ว่าง = ไม่จำกัด", "empty = unlimited")}>
        <input inputMode="decimal" value={value.maxWeightPct} disabled={disabled} onChange={(e) => set("maxWeightPct", e.target.value)} className={input} />
      </Field>
      <Field label={t("Position สูงสุด", "Max positions")}>
        <input inputMode="numeric" value={value.maxPositions} disabled={disabled} onChange={(e) => set("maxPositions", e.target.value)} className={input} />
      </Field>
      <Field label={t("ค่าคอมฯ ต่อข้าง (%)", "Commission per side (%)")} hint={t("ค่าสมมติ — ใส่ตามโบรกเกอร์ของคุณ", "An assumption — use your broker's rate")}>
        <input
          data-testid="sim-commission"
          inputMode="decimal"
          value={value.commissionPct}
          disabled={disabled}
          onChange={(e) => set("commissionPct", e.target.value)}
          className={input}
        />
      </Field>
      <Field label="Slippage (bps)" hint={t("ใช้กับ market/stop order และแทน spread ของ forex", "Applied to market/stop orders and stands in for the forex spread")}>
        <input inputMode="decimal" value={value.slippageBps} disabled={disabled} onChange={(e) => set("slippageBps", e.target.value)} className={input} />
      </Field>
      <label className="flex items-center gap-1.5 self-end pb-1 text-[12.5px]">
        <input type="checkbox" checked={value.compounding} disabled={disabled} onChange={(e) => set("compounding", e.target.checked)} />
        {t("ทบต้น (คิดขนาดจาก equity ปัจจุบัน)", "Compounding (size from current equity)")}
      </label>
      <label className="flex items-center gap-1.5 self-end pb-1 text-[12.5px]">
        <input type="checkbox" checked={value.allowShortForex} disabled={disabled} onChange={(e) => set("allowShortForex", e.target.checked)} />
        {t("ชอร์ต forex/ทองได้", "Allow shorting forex/gold")}
      </label>
      {Number(value.commissionPct) === 0 ? (
        <p data-testid="no-fee-warning" className="col-span-full text-[11.5px] text-warning">
          {t(
            "ค่าคอมมิชชันเป็น 0 — ผลจะดีกว่าความจริง ใส่ % ของโบรกเกอร์ที่คุณใช้เพื่อความสมจริง",
            "Commission is 0 — results will look better than reality. Enter your broker's % for a realistic result",
          )}
        </p>
      ) : null}
    </div>
  );
}
