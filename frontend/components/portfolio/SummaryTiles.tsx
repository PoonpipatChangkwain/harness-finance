"use client";

/** Headline tiles of one book (ui-spec §6): every number from analytics, with provenance. */

import type { ReactNode } from "react";

import type { PortfolioAnalytics } from "@/lib/portfolio-api";

import { ProvValue } from "./ProvValue";

function Tile({ label, children, sub }: { label: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-layer-1 px-3 py-2">
      <div className="text-[11.5px] text-muted">{label}</div>
      <div className="mt-0.5 truncate text-[16px] font-semibold">{children}</div>
      {sub ? <div className="text-[11.5px]">{sub}</div> : null}
    </div>
  );
}

export function SummaryTiles({ analytics }: { analytics: PortfolioAnalytics }) {
  const a = analytics;
  const ccy = <span className="ml-1 text-[11px] font-normal text-subtle">{a.base_currency}</span>;
  return (
    <div data-testid="summary-tiles" className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
      <Tile label="มูลค่ารวม">
        {a.weights_declared_only ? <span className="text-subtle">–</span> : <ProvValue pv={a.total_value} suffix={ccy} />}
      </Tile>
      <Tile label="เงินสด">
        {a.cash ? <ProvValue pv={a.cash} suffix={ccy} /> : <span className="text-[13px] text-subtle">ไม่ได้ระบุ</span>}
      </Tile>
      <Tile label="P&L วันนี้" sub={<ProvValue pv={a.day_pnl_pct} format="signedPct" colored />}>
        <ProvValue pv={a.day_pnl} format="signed" colored />
      </Tile>
      <Tile label="P&L รวม (ยังไม่รับรู้)" sub={<ProvValue pv={a.unrealized_pnl_pct} format="signedPct" colored />}>
        <ProvValue pv={a.unrealized_pnl} format="signed" colored />
      </Tile>
      <Tile label="Positions">
        <span className="num">{a.positions_count}</span>
      </Tile>
      <Tile label="สกุลเงินหลัก" sub={a.as_of ? <span className="text-subtle">ราคา ณ {a.as_of}</span> : null}>
        {a.base_currency}
      </Tile>
    </div>
  );
}
