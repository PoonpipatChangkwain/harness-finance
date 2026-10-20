"use client";

/** Ledger of simulated trades + why the other decisions did not trade (P15 §UI 3). */

import Link from "next/link";

import { EXIT_LABEL, SKIP_LABEL } from "@/lib/backtest";
import type { SimResult } from "@/lib/backtest-api";
import { useT } from "@/lib/i18n";
import { formatMoney, upDownClass } from "@/lib/portfolio-format";

const px = (s: string) => Number(s).toLocaleString(undefined, { maximumFractionDigits: 4 });

export function TradesTable({ result }: { result: SimResult }) {
  const t = useT();
  const skippedBy = Object.entries(
    result.skipped.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s.reason]: (acc[s.reason] ?? 0) + 1 }), {}),
  ).sort((a, b) => b[1] - a[1]);
  return (
    <div className="grid gap-3 xl:grid-cols-[1fr_280px]">
      <div className="overflow-x-auto rounded-md border border-line bg-layer-1">
        <table data-testid="trades-table" className="w-full text-[12.5px]">
          <thead className="text-left text-[11.5px] text-muted">
            <tr className="border-b border-line">
              {[
                "Symbol",
                t("ฝั่ง", "Side"),
                t("ตัดสินใจ", "Decided"),
                t("เข้า", "Entry"),
                t("ออก", "Exit"),
                t("จำนวน", "Units"),
                t("เหตุผลออก", "Exit reason"),
                "R",
                "P&L",
              ].map((h) => (
                <th key={h} className="px-2 py-1.5 font-normal">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.trades.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-2 py-3 text-center text-subtle">
                  {t("ไม่มีเทรดที่เกิดขึ้นจริง — ดูเหตุผลทางขวา", "No trades actually happened — see the reasons on the right")}
                </td>
              </tr>
            ) : null}
            {result.trades.map((t, i) => (
              <tr key={`${t.ref}-${i}`} className="border-b border-line/60 last:border-0">
                <td className="px-2 py-1">
                  {t.ref.startsWith("run_") ? (
                    <Link href={`/trade/${t.ref}`} className="text-accent hover:underline">
                      {t.symbol}
                    </Link>
                  ) : (
                    t.symbol
                  )}
                </td>
                <td className={`px-2 py-1 ${t.side === "long" ? "text-up" : "text-down"}`}>{t.side}</td>
                <td className="num px-2 py-1 text-muted">{t.decided_on}</td>
                <td className="num px-2 py-1">
                  {px(t.entry_price)} <span className="text-subtle">{t.entry_at.slice(0, 10)}</span>
                </td>
                <td className="num px-2 py-1">
                  {px(t.exit_price)} <span className="text-subtle">{t.exit_at.slice(0, 10)}</span>
                </td>
                <td className="num px-2 py-1">{Number(t.units).toLocaleString()}</td>
                <td className="px-2 py-1">
                  {EXIT_LABEL[t.exit_reason] ?? t.exit_reason}
                  {t.gap ? <span className="ml-1 text-warning">gap</span> : null}
                </td>
                <td className={`num px-2 py-1 ${upDownClass(Number(t.r_multiple))}`}>{t.r_multiple === null ? "–" : Number(t.r_multiple).toFixed(2)}</td>
                <td className={`num px-2 py-1 ${upDownClass(Number(t.pnl))}`}>{formatMoney(Number(t.pnl), 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div data-testid="skipped-summary" className="rounded-md border border-line bg-layer-1 p-3 text-[12.5px]">
        <div className="mb-1 font-medium">
          {t("การตัดสินใจที่ไม่ได้เทรด", "Decisions not traded")} ({result.skipped.length})
        </div>
        {skippedBy.length === 0 ? <p className="text-subtle">–</p> : null}
        <ul className="space-y-0.5">
          {skippedBy.map(([reason, n]) => (
            <li key={reason} className="flex justify-between gap-2">
              <span className="text-muted">{SKIP_LABEL[reason] ?? reason}</span>
              <span className="num">{n}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
