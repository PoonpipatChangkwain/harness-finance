"use client";

/** Equity curve vs buy & hold vs the SMA baseline, with the drawdown band under it (P15 §UI 3). */

import { clsx } from "clsx";
import { useState } from "react";
import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { drawdown, mergeCurves } from "@/lib/backtest";
import type { SimResult } from "@/lib/backtest-api";
import { useT } from "@/lib/i18n";
import { formatMoney } from "@/lib/portfolio-format";

const COLORS = { strategy: "var(--fh-accent)", benchmark: "var(--fh-text-subtle)", baseline: "var(--fh-state-warning)" };

export function EquityChart({ result, currency }: { result: SimResult; currency: string }) {
  const t = useT();
  const [mode, setMode] = useState<"money" | "percent">("money");
  const rows = mergeCurves(result.equity_curve, result.benchmark, result.baseline?.equity_curve ?? [], mode);
  const dd = drawdown(result.equity_curve);
  const fmt = (v: number) => (mode === "percent" ? `${v.toFixed(1)}%` : formatMoney(v, 0));
  if (!rows.length) return <p className="text-[12.5px] text-subtle">{t("ไม่มีข้อมูล equity", "No equity data")}</p>;
  return (
    <div data-testid="equity-chart" className="rounded-md border border-line bg-layer-1 p-3">
      <div className="mb-2 flex items-center gap-2 text-[12.5px]">
        <span className="font-medium">{t("มูลค่าพอร์ต", "Portfolio value")}</span>
        <span className="text-subtle">({mode === "money" ? currency : t("% จากจุดเริ่ม", "% from start")})</span>
        <div className="ml-auto flex rounded-md border border-line">
          {(["money", "percent"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={clsx("px-2 py-0.5 text-[12px]", mode === m ? "bg-layer-2 text-fg" : "text-muted")}
            >
              {m === "money" ? currency : "%"}
            </button>
          ))}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="var(--fh-border)" strokeDasharray="3 3" />
          <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--fh-text-subtle)" }} minTickGap={40} />
          <YAxis tickFormatter={fmt} tick={{ fontSize: 11, fill: "var(--fh-text-subtle)" }} width={72} domain={["auto", "auto"]} />
          <Tooltip
            formatter={(v) => fmt(Number(v))}
            contentStyle={{ background: "var(--fh-bg-layer-1)", border: "1px solid var(--fh-border)", fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="strategy" name={t("ระบบเทรด (LLM)", "Trading system (LLM)")} stroke={COLORS.strategy} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          <Line type="monotone" dataKey="benchmark" name="Buy & hold" stroke={COLORS.benchmark} strokeDasharray="4 3" dot={false} connectNulls isAnimationActive={false} />
          {result.baseline ? (
            <Line type="monotone" dataKey="baseline" name={result.baseline.name} stroke={COLORS.baseline} dot={false} connectNulls isAnimationActive={false} />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
      <div className="mt-1 text-[11.5px] text-muted">Drawdown</div>
      <ResponsiveContainer width="100%" height={80}>
        <AreaChart data={dd} margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
          <XAxis dataKey="day" hide />
          <YAxis tickFormatter={(v: number) => `${v.toFixed(0)}%`} tick={{ fontSize: 10, fill: "var(--fh-text-subtle)" }} width={72} />
          <Tooltip formatter={(v) => `${Number(v).toFixed(2)}%`} contentStyle={{ background: "var(--fh-bg-layer-1)", border: "1px solid var(--fh-border)", fontSize: 12 }} />
          <Area type="monotone" dataKey="dd" name="drawdown" stroke="var(--fh-down)" fill="var(--fh-down)" fillOpacity={0.2} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
