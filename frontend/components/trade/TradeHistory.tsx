"use client";

/** Sidebar on /trade (phase-11 §11.3): run history `symbol · date · rating · verdict · StateDot` + symbol filter. */

import { clsx } from "clsx";
import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StateDot, dotFor } from "@/components/ui/StateDot";
import { formatRelative } from "@/lib/format";
import { useT } from "@/lib/i18n";
import type { TradeRunBrief } from "@/lib/trade-api";
import { useTradeHistory } from "@/stores/trade-store";

import { VerdictBadge } from "./DecisionCard";

const OPEN = ["running", "waiting_approval"];

export function TradeHistory({ compact, onNavigate }: { compact: boolean; onNavigate?: () => void }) {
  const t = useT();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const { runs, loaded, error, symbol, setSymbol, reload, remove } = useTradeHistory();
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    void reload();
  }, [reload, pathname]);

  const onDelete = async (run: TradeRunBrief) => {
    const label = run.symbol ?? run.run_id;
    const message = t(
      `ลบ trade run "${label}"?\nข้อมูล run, provenance และ scratchpad ของ run นี้จะถูกลบถาวร`,
      `Delete trade run "${label}"?\nIts run data, provenance and scratchpad will be permanently deleted.`,
    );
    if (!window.confirm(message)) return;
    const err = await remove(run.run_id);
    setDeleteError(err);
    if (!err && pathname === `/trade/${run.run_id}`) router.push("/trade");
  };

  // keep open runs' status fresh
  const anyOpen = runs.some((r) => OPEN.includes(r.status));
  useEffect(() => {
    if (!anyOpen) return;
    const id = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(id);
  }, [anyOpen, reload]);

  return (
    <>
      <div className={clsx("mt-4 flex items-center px-3", compact ? "justify-center px-0" : "justify-between")}>
        {compact ? null : <span className="text-[11px] uppercase tracking-wide text-subtle">Trade runs</span>}
        <Link
          href="/trade"
          title="New trade analysis"
          onClick={onNavigate}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-accent hover:bg-layer-2"
        >
          <Plus className="size-3.5" aria-hidden />
          {compact ? null : "New"}
        </Link>
      </div>
      {compact ? null : (
        <div className="px-2 pt-1">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder={t("กรอง symbol", "Filter symbol")}
            aria-label="filter trade runs by symbol"
            // browser extensions (e.g. form fillers) inject `fdprocessedid` before hydration
            suppressHydrationWarning
            className="w-full rounded-md border border-line bg-bg px-2 py-1 text-[12px] text-fg placeholder:text-subtle"
          />
        </div>
      )}
      <ul data-testid="trade-history" className="mt-1 min-h-0 flex-1 space-y-px overflow-y-auto px-2">
        {!loaded ? <li className="px-2 text-[12px] text-subtle">{t("กำลังโหลด…", "Loading…")}</li> : null}
        {error && !compact ? <li className="px-2 text-[12px] text-error">{t("โหลดประวัติไม่ได้", "Could not load history")}</li> : null}
        {deleteError && !compact ? <li className="px-2 text-[12px] text-error">{deleteError}</li> : null}
        {loaded && !runs.length && !compact ? <li className="px-2 text-[12px] text-subtle">{t("ยังไม่มี run", "No runs yet")}</li> : null}
        {runs.map((r) => {
          const href = `/trade/${r.run_id}`;
          const status = r.status === "waiting_approval" ? "partial" : r.status;
          return (
            <li key={r.run_id} className="group relative">
              <Link
                href={href}
                onClick={onNavigate}
                title={`${r.symbol ?? ""} · ${r.status}`}
                className={clsx(
                  "flex items-center gap-2 rounded-md px-2 py-1.5",
                  compact ? "justify-center" : "pr-8",
                  pathname === href ? "bg-layer-2" : "hover:bg-layer-2",
                )}
              >
                <StateDot state={dotFor(status)} label={r.status} />
                {compact ? null : (
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-[13px] text-fg">
                      <span className="num truncate">{r.symbol ?? "?"}</span>
                      {r.rating ? <span className="text-[11px] text-accent">{r.rating}</span> : null}
                      {r.verdict ? <VerdictBadge verdict={r.verdict} /> : null}
                    </span>
                    <span className="block text-[11px] text-subtle">
                      {r.date ?? "latest"} · {r.status === "waiting_approval" ? t("รออนุมัติ", "awaiting approval") : (r.outcome ?? r.status)} ·{" "}
                      {formatRelative(r.created_at)}
                    </span>
                  </span>
                )}
              </Link>
              {compact ? null : (
                <button
                  type="button"
                  data-testid="delete-trade-run"
                  aria-label={t(`ลบ trade run ${r.symbol ?? r.run_id}`, `Delete trade run ${r.symbol ?? r.run_id}`)}
                  title={t("ลบ run", "Delete run")}
                  onClick={() => void onDelete(r)}
                  className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1 text-subtle opacity-0 hover:bg-error/15 hover:text-error focus-visible:opacity-100 group-hover:opacity-100"
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
