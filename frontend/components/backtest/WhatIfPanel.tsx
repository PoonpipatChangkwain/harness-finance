"use client";

/**
 * What-if (P15 §UI 3): the same decisions replayed with another capital / risk / cost —
 * a server-side simulation, no LLM call. Every simulation is kept and can be switched back to.
 */

import { clsx } from "clsx";
import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { simFormFrom, toSimConfig, type SimForm } from "@/lib/backtest";
import { backtestApi, type Sim, type SimBrief } from "@/lib/backtest-api";
import { formatMoney, formatPct } from "@/lib/portfolio-format";

import { SimFields } from "./SimFields";

export function WhatIfPanel({
  backtestId,
  sims,
  current,
  disabled,
  onSim,
  onPick,
}: {
  backtestId: string;
  sims: SimBrief[];
  current: Sim | null;
  disabled?: boolean;
  onSim: (sim: Sim) => void;
  onPick: (simId: string) => void;
}) {
  const [form, setForm] = useState<SimForm>(() => simFormFrom(current?.config));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // follow the simulation on screen (e.g. picked from the list)
  useEffect(() => {
    if (current) setForm(simFormFrom(current.config));
  }, [current]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      onSim(await backtestApi.simulate(backtestId, toSimConfig(form)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="what-if" className="space-y-2 rounded-md border border-line bg-layer-1 p-3">
      <div className="flex items-center gap-2">
        <h2 className="text-[13.5px] font-medium">ถ้าเปลี่ยนเงินต้น/ความเสี่ยง จะได้เท่าไหร่?</h2>
        <span className="text-[11.5px] text-subtle">ใช้การตัดสินใจชุดเดิม ไม่เรียก LLM</span>
      </div>
      <SimFields value={form} onChange={setForm} disabled={busy || disabled} />
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="what-if-run"
          disabled={busy || disabled}
          onClick={() => void run()}
          className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[13px] font-medium text-bg disabled:opacity-50"
        >
          <RefreshCw className={clsx("size-3.5", busy && "animate-spin")} aria-hidden /> จำลองใหม่
        </button>
        {error ? (
          <span role="alert" className="text-[12.5px] text-error">
            {error}
          </span>
        ) : null}
      </div>
      {sims.length > 1 ? (
        <div className="overflow-x-auto">
          <table data-testid="sims-table" className="w-full text-[12px]">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 font-normal">เงินต้น</th>
                <th className="font-normal">เสี่ยง/เทรด</th>
                <th className="font-normal">ค่าคอมฯ</th>
                <th className="font-normal">เงินสุดท้าย</th>
                <th className="font-normal">Return</th>
                <th className="font-normal">เทรด</th>
              </tr>
            </thead>
            <tbody>
              {[...sims].reverse().map((s) => {
                const f = simFormFrom(s.config);
                const active = s.sim_id === current?.sim_id;
                return (
                  <tr
                    key={s.sim_id}
                    onClick={() => onPick(s.sim_id)}
                    className={clsx("num cursor-pointer border-t border-line/60", active ? "bg-layer-2" : "hover:bg-layer-2")}
                  >
                    <td className="py-1">
                      {formatMoney(Number(f.capital), 0)} {f.baseCurrency}
                    </td>
                    <td>{f.riskPct}%</td>
                    <td>{f.commissionPct}%</td>
                    <td>{formatMoney(Number(s.metrics.final_equity), 0)}</td>
                    <td>{formatPct(s.metrics.total_return, 2, true)}</td>
                    <td>{s.metrics.trades}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
