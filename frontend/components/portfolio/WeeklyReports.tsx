"use client";

/**
 * "รายงาน AI รายสัปดาห์" (phase-13 §13.6): every Friday 03:00 Asia/Bangkok the backend
 * snapshots each book and runs the risk review; "รันตอนนี้" does the same on demand.
 * Each report is an ordinary portfolio chat session, opened in the Research view.
 */

import { CalendarClock, Play } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StateDot, dotFor } from "@/components/ui/StateDot";
import { formatRelative } from "@/lib/format";
import { portfolioApi, type WeeklyReports as Reports } from "@/lib/portfolio-api";

import { ProvValue } from "./ProvValue";

const BKK = { timeZone: "Asia/Bangkok", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" } as const;

export function WeeklyReports({
  portfolioId,
  names,
  revision,
}: {
  /** one book, or undefined for every book */
  portfolioId?: string;
  names: Map<string, string>;
  revision: number;
}) {
  const [data, setData] = useState<Reports | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    portfolioApi
      .reports(portfolioId)
      .then((r) => {
        setData(r);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [portfolioId]);
  useEffect(load, [load, revision]);

  // poll while a report run is still going
  const running = data?.reports.some((r) => r.run_status === "running" || r.run_status === "pending") ?? false;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [running, load]);

  const runNow = async () => {
    setBusy(true);
    try {
      await portfolioApi.runReports(portfolioId ? [portfolioId] : undefined);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const schedule = data?.schedule;
  return (
    <section data-testid="weekly-reports" className="rounded-md border border-line bg-layer-1 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <CalendarClock className="size-4 text-muted" aria-hidden />
        <h2 className="text-[13px] font-semibold">รายงาน AI รายสัปดาห์</h2>
        {schedule ? (
          <span className="text-[11.5px] text-subtle">
            {schedule.enabled
              ? `ทุกศุกร์ 03:00 (เวลาไทย) · ครั้งถัดไป ${new Date(schedule.next_run).toLocaleString("th-TH", BKK)}`
              : "ปิดอยู่ (PORTFOLIO_WEEKLY_ENABLED=false)"}
          </span>
        ) : null}
        <span className="flex-1" />
        <button
          type="button"
          data-testid="run-report-now"
          disabled={busy}
          onClick={() => void runNow()}
          className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-0.5 text-[12px] hover:text-fg disabled:opacity-50"
        >
          <Play className="size-3.5" aria-hidden /> {busy ? "กำลังเริ่ม…" : "รันตอนนี้"}
        </button>
      </div>
      {error ? <p className="text-[12px] text-error">{error}</p> : null}
      {data && data.reports.length === 0 ? <p className="text-[12px] text-subtle">ยังไม่มีรายงาน</p> : null}
      <ul className="space-y-0.5">
        {data?.reports.slice(0, 12).map((r) => {
          const s = r.summary;
          return (
            <li key={r.id} data-testid="weekly-report" className="flex flex-wrap items-center gap-2 rounded-md px-1 py-1 text-[12.5px] hover:bg-layer-2">
              <StateDot state={dotFor(r.run_status ?? (s.error ? "failed" : null))} label={r.run_status ?? "no run"} />
              <span className="text-muted">{formatRelative(r.taken_at)}</span>
              {!portfolioId ? <span className="font-medium">{names.get(r.portfolio_id) ?? r.portfolio_id}</span> : null}
              <span className="rounded-sm bg-layer-2 px-1 text-[10.5px] text-subtle">{r.kind}</span>
              {s.total_value ? (
                <span>
                  <ProvValue pv={s.total_value} /> <span className="text-[11px] text-subtle">{s.base_currency}</span>
                </span>
              ) : null}
              {s.unrealized_pnl_pct ? <ProvValue pv={s.unrealized_pnl_pct} format="signedPct" colored /> : null}
              {s.error || s.run_error ? <span className="text-[11.5px] text-error">{s.error ?? s.run_error}</span> : null}
              <span className="flex-1" />
              {r.session_id ? (
                <Link href={`/research/${r.session_id}`} className="text-[12px] text-accent hover:underline">
                  ดูรายงาน
                </Link>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
