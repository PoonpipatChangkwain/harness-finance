"use client";

/** Sidebar on /backtest (P15 §UI 4): past backtests with status and result. */

import { clsx } from "clsx";
import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { StateDot, dotFor } from "@/components/ui/StateDot";
import { backtestApi, type BacktestBrief } from "@/lib/backtest-api";
import { formatRelative } from "@/lib/format";
import { formatPct, upDownClass } from "@/lib/portfolio-format";

export function BacktestHistory({ compact, onNavigate }: { compact: boolean; onNavigate?: () => void }) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const [items, setItems] = useState<BacktestBrief[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setItems((await backtestApi.list()).backtests);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, pathname]);

  const anyRunning = items.some((b) => b.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const t = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(t);
  }, [anyRunning, reload]);

  const onDelete = async (b: BacktestBrief) => {
    if (!window.confirm(`ลบ backtest "${b.name || b.symbols.join(", ")}"?\n(trading run ของแต่ละวันจะยังอยู่ และใช้เป็น cache ได้)`)) return;
    try {
      await backtestApi.remove(b.backtest_id);
      if (pathname === `/backtest/${b.backtest_id}`) router.push("/backtest");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <div className={clsx("mt-4 flex items-center px-3", compact ? "justify-center px-0" : "justify-between")}>
        {compact ? null : <span className="text-[11px] uppercase tracking-wide text-subtle">Backtests</span>}
        <Link href="/backtest" title="New backtest" onClick={onNavigate} className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-accent hover:bg-layer-2">
          <Plus className="size-3.5" aria-hidden />
          {compact ? null : "New"}
        </Link>
      </div>
      <ul data-testid="backtest-history" className="mt-1 min-h-0 flex-1 space-y-px overflow-y-auto px-2">
        {error && !compact ? <li className="px-2 text-[12px] text-error">{error}</li> : null}
        {items.map((b) => {
          const href = `/backtest/${b.backtest_id}`;
          return (
            <li key={b.backtest_id} className="group relative">
              <Link
                href={href}
                onClick={onNavigate}
                title={b.symbols.join(", ")}
                className={clsx("flex items-center gap-2 rounded-md px-2 py-1.5", compact ? "justify-center" : "pr-8", pathname === href ? "bg-layer-2" : "hover:bg-layer-2")}
              >
                <StateDot state={dotFor(b.status)} label={b.status} />
                {compact ? null : (
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px]">{b.name || b.symbols.join(", ")}</span>
                    <span className="flex gap-2 text-[11px] text-subtle">
                      <span>{formatRelative(b.created_at)}</span>
                      {b.total_return !== null ? <span className={upDownClass(b.total_return)}>{formatPct(b.total_return, 1, true)}</span> : null}
                    </span>
                  </span>
                )}
              </Link>
              {compact ? null : (
                <button
                  type="button"
                  aria-label={`ลบ backtest ${b.name || b.backtest_id}`}
                  onClick={() => void onDelete(b)}
                  className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1 text-subtle opacity-0 hover:bg-error/15 hover:text-error group-hover:opacity-100"
                >
                  <Trash2 className="size-3.5" aria-hidden />
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}
