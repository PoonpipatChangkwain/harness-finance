"use client";

/** The portfolio part of a backtest (P15): capital, risk, costs. Used by the setup and the what-if panel. */

import type { ReactNode } from "react";

import type { SimForm } from "@/lib/backtest";

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
  const set = <K extends keyof SimForm>(k: K, v: SimForm[K]) => onChange({ ...value, [k]: v });
  return (
    <div data-testid="sim-fields" className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4">
      <Field label="เงินต้นพอร์ต">
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
            aria-label="สกุลเงินของพอร์ต"
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
      <Field label="เสี่ยงต่อเทรด (%)" hint="ส่วนของเงินในพอร์ตที่ยอมเสียถ้าชน stop">
        <input inputMode="decimal" value={value.riskPct} disabled={disabled} onChange={(e) => set("riskPct", e.target.value)} className={input} />
      </Field>
      <Field label="น้ำหนักสูงสุดต่อตัว (%)" hint="ว่าง = ไม่จำกัด">
        <input inputMode="decimal" value={value.maxWeightPct} disabled={disabled} onChange={(e) => set("maxWeightPct", e.target.value)} className={input} />
      </Field>
      <Field label="Position สูงสุด">
        <input inputMode="numeric" value={value.maxPositions} disabled={disabled} onChange={(e) => set("maxPositions", e.target.value)} className={input} />
      </Field>
      <Field label="ค่าคอมฯ ต่อข้าง (%)" hint="ค่าสมมติ — ใส่ตามโบรกเกอร์ของคุณ">
        <input
          data-testid="sim-commission"
          inputMode="decimal"
          value={value.commissionPct}
          disabled={disabled}
          onChange={(e) => set("commissionPct", e.target.value)}
          className={input}
        />
      </Field>
      <Field label="Slippage (bps)" hint="ใช้กับ market/stop order และแทน spread ของ forex">
        <input inputMode="decimal" value={value.slippageBps} disabled={disabled} onChange={(e) => set("slippageBps", e.target.value)} className={input} />
      </Field>
      <label className="flex items-center gap-1.5 self-end pb-1 text-[12.5px]">
        <input type="checkbox" checked={value.compounding} disabled={disabled} onChange={(e) => set("compounding", e.target.checked)} />
        ทบต้น (คิดขนาดจาก equity ปัจจุบัน)
      </label>
      <label className="flex items-center gap-1.5 self-end pb-1 text-[12.5px]">
        <input type="checkbox" checked={value.allowShortForex} disabled={disabled} onChange={(e) => set("allowShortForex", e.target.checked)} />
        ชอร์ต forex/ทองได้
      </label>
      {Number(value.commissionPct) === 0 ? (
        <p data-testid="no-fee-warning" className="col-span-full text-[11.5px] text-warning">
          ค่าคอมมิชชันเป็น 0 — ผลจะดีกว่าความจริง ใส่ % ของโบรกเกอร์ที่คุณใช้เพื่อความสมจริง
        </p>
      ) : null}
    </div>
  );
}
