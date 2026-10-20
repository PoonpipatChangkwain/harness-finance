"use client";

/** Headline numbers of one simulation (P15 §UI 3): every value computed by the backend simulator. */

import { clsx } from "clsx";
import type { ReactNode } from "react";

import type { SimMetrics } from "@/lib/backtest-api";
import { useT } from "@/lib/i18n";
import { formatMoney, formatPct, upDownClass } from "@/lib/portfolio-format";

function Tile({ label, children, sub, testId }: { label: string; children: ReactNode; sub?: ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="min-w-0 rounded-md border border-line bg-layer-1 px-3 py-2">
      <div className="text-[11.5px] text-muted">{label}</div>
      <div className="num mt-0.5 truncate text-[16px] font-semibold">{children}</div>
      {sub ? <div className="num text-[11.5px] text-muted">{sub}</div> : null}
    </div>
  );
}

const pct = (v: number | null | undefined, signed = false) => (v === null || v === undefined ? "–" : formatPct(v, 2, signed));
const ratio = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v.toFixed(2));

export function ResultTiles({ m }: { m: SimMetrics }) {
  const t = useT();
  const ccy = <span className="ml-1 text-[11px] font-normal text-subtle">{m.currency}</span>;
  const skipped = Object.values(m.skipped).reduce((a, b) => a + b, 0);
  return (
    <div data-testid="result-tiles" className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
      <Tile label={t("เงินต้น → เงินสุดท้าย", "Start → final equity")} testId="tile-final" sub={<>{t("เริ่ม", "start")} {formatMoney(Number(m.initial_capital), 0)}</>}>
        {formatMoney(Number(m.final_equity), 0)}
        {ccy}
      </Tile>
      <Tile label={t("กำไร/ขาดทุน", "Profit/loss")} sub={<span className={upDownClass(m.total_return)}>{pct(m.total_return, true)}</span>}>
        <span className={upDownClass(Number(m.profit))}>{formatMoney(Number(m.profit), 0)}</span>
      </Tile>
      <Tile label="Buy & hold" sub={m.alpha !== null && m.alpha !== undefined ? <>alpha {pct(m.alpha, true)}</> : null}>
        <span className={upDownClass(m.benchmark_return ?? null)}>{pct(m.benchmark_return, true)}</span>
      </Tile>
      <Tile label="CAGR">{pct(m.cagr, true)}</Tile>
      <Tile label="Max drawdown">
        <span className={clsx(m.max_drawdown < 0 && "text-down")}>{pct(m.max_drawdown)}</span>
      </Tile>
      <Tile label="Sharpe / Sortino">
        {ratio(m.sharpe)} / {ratio(m.sortino)}
      </Tile>
      <Tile label={t("เทรด · ชนะ", "Trades · win")} sub={<>PF {ratio(m.profit_factor)} · avg {m.expectancy_r === null ? "–" : `${m.expectancy_r.toFixed(2)}R`}</>}>
        {m.trades} · {pct(m.win_rate)}
      </Tile>
      <Tile label={t("ข้าม · ค่าธรรมเนียม", "Skipped · fees")} sub={<>{t(`ถือ position ${pct(m.exposure)} ของเวลา`, `in a position ${pct(m.exposure)} of the time`)}</>}>
        {skipped} · {formatMoney(Number(m.fees), 0)}
      </Tile>
    </div>
  );
}
