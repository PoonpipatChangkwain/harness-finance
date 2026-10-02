"use client";

/**
 * Scheduled releases over the trade's holding period (ForexFactory, via state.event_risk).
 * Times are shown in the viewer's local time; high-impact rows are highlighted because a
 * release like CPI or NFP can run through any stop on a short-term trade.
 */

import { clsx } from "clsx";

import type { EventRisk } from "@/lib/trade-api";

const IMPACT_CLASS: Record<string, string> = {
  high: "bg-down/15 text-down",
  medium: "bg-warning/15 text-warning",
  low: "bg-layer-2 text-muted",
  holiday: "bg-layer-2 text-muted",
};

function fmtHours(h: number): string {
  if (Math.abs(h) < 1) return `${Math.round(h * 60)} นาที`;
  return `${h > 0 ? "อีก " : ""}${Math.abs(h).toFixed(1)} ชม.${h < 0 ? "ที่แล้ว" : ""}`;
}

export function EventRiskList({ risk }: { risk: EventRisk | null | undefined }) {
  if (!risk) return null;
  const events = (risk.events ?? []).filter((e) => e.impact !== "low");
  return (
    <section data-testid="event-risk" className="rounded-md border border-line bg-layer-1 p-3">
      <h3 className="mb-1.5 flex items-center gap-2 text-[12px] font-medium uppercase tracking-wide text-subtle">
        ข่าวเศรษฐกิจช่วงถือ
        {risk.available ? (
          <span className="normal-case tracking-normal">
            {risk.source} · {(risk.currencies ?? []).join("/")} · {risk.window_hours} ชม.ข้างหน้า
          </span>
        ) : null}
      </h3>
      {!risk.available ? (
        <p className="text-[12.5px] text-warning">ปฏิทินข่าวไม่พร้อม: {risk.error ?? "ไม่ได้โหลด"} (ไม่ได้แปลว่าไม่มีข่าว)</p>
      ) : events.length === 0 ? (
        <p className="text-[12.5px] text-muted">ไม่มีข่าวแรงระดับ high/medium ในช่วงถือ</p>
      ) : (
        <ul className="space-y-1 text-[12.5px]">
          {events.map((e) => (
            <li key={`${e.at}-${e.currency}-${e.title}`} data-testid="event-row" data-impact={e.impact} className="flex flex-wrap items-baseline gap-2">
              <span className={clsx("rounded-sm px-1 text-[11px] font-semibold uppercase", IMPACT_CLASS[e.impact])}>{e.impact}</span>
              <span className="num w-10 text-muted">{e.currency}</span>
              <span className="min-w-0 flex-1">{e.title}</span>
              <span className="num text-subtle">
                {new Date(e.at).toLocaleString("th-TH", { weekday: "short", hour: "2-digit", minute: "2-digit" })} ·{" "}
                {fmtHours(e.hours_from_now)}
              </span>
              {e.forecast || e.previous || e.actual ? (
                <span className="num basis-full pl-[3.25rem] text-[11px] text-subtle">
                  {e.actual ? `actual ${e.actual} · ` : ""}forecast {e.forecast ?? "–"} · previous {e.previous ?? "–"}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
