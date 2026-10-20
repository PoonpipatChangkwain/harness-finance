"use client";

/** Symbol × analysis-day grid (P15 §UI 2): one square per trading run, coloured by what it decided. */

import { clsx } from "clsx";
import Link from "next/link";

import { cellMatrix, cellTone, type CellTone } from "@/lib/backtest";
import type { BacktestCell } from "@/lib/backtest-api";
import { localized, tr, useT } from "@/lib/i18n";

const TONE: Record<CellTone, string> = {
  pending: "bg-layer-2",
  running: "animate-pulse bg-accent/60",
  long: "bg-up",
  short: "bg-down",
  flat: "bg-subtle/40",
  failed: "bg-error ring-1 ring-error",
};

const TONE_LABEL: Record<CellTone, string> = localized({
  pending: ["รอ", "Pending"],
  running: ["กำลังรัน", "Running"],
  long: ["Long", "Long"],
  short: ["Short", "Short"],
  flat: ["ไม่เทรด", "No trade"],
  failed: ["ล้มเหลว", "Failed"],
});

function title(c: BacktestCell): string {
  const d = c.decision;
  const parts = [`${c.symbol} · ${c.day}`, TONE_LABEL[cellTone(c)]];
  if (d?.rating) parts.push(d.rating);
  if (d?.outcome && d.outcome !== "approved") parts.push(d.outcome);
  if (c.cached_from) parts.push(tr("ใช้ผลเดิม (cache)", "reused (cache)"));
  if (c.error) parts.push(c.error);
  return parts.join(" · ");
}

export function CellGrid({ cells }: { cells: BacktestCell[] }) {
  const t = useT();
  const { symbols, days, at } = cellMatrix(cells);
  if (!cells.length) return <p className="text-[12.5px] text-subtle">{t("ยังไม่มี cell — กำลังโหลดราคา…", "No cells yet — loading prices…")}</p>;
  return (
    <div data-testid="cell-grid" className="space-y-2">
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-0.5">
          <thead>
            <tr>
              <th />
              {days.map((d, i) => (
                <th key={d} className="px-0 text-[10px] font-normal text-subtle">
                  {i % 5 === 0 ? d.slice(5) : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((s) => (
              <tr key={s}>
                <th scope="row" className="pr-2 text-left text-[12px] font-medium">
                  {s}
                </th>
                {days.map((d) => {
                  const c = at(s, d);
                  if (!c) return <td key={d} />;
                  const tone = cellTone(c);
                  const square = (
                    <span
                      data-testid="cell"
                      data-tone={tone}
                      title={title(c)}
                      className={clsx("block size-4 rounded-[3px]", TONE[tone], c.cached_from && "opacity-70")}
                    />
                  );
                  return (
                    <td key={d}>
                      {c.run_id ? (
                        <Link href={`/trade/${c.run_id}`} aria-label={title(c)}>
                          {square}
                        </Link>
                      ) : (
                        square
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-3 text-[11.5px] text-muted">
        {(Object.keys(TONE) as CellTone[]).map((t) => (
          <span key={t} className="inline-flex items-center gap-1">
            <span className={clsx("inline-block size-3 rounded-[3px]", TONE[t])} /> {TONE_LABEL[t]}
          </span>
        ))}
        <span>· {t("คลิกช่องเพื่อดู pipeline ของวันนั้น", "click a cell to see that day's pipeline")}</span>
      </div>
    </div>
  );
}
