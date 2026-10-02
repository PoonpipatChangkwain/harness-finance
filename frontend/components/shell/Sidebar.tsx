"use client";

/** Left column (ui-spec §3): logo, nav, research sessions (+ New), theme and provider status. */

import { clsx } from "clsx";
import { Briefcase, ChartCandlestick, FlaskConical, History, Plus, Settings, Trash2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { BacktestHistory } from "@/components/backtest/BacktestHistory";
import { TradeHistory } from "@/components/trade/TradeHistory";
import { StateDot, dotFor } from "@/components/ui/StateDot";
import type { SessionListItem } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { useSessionStore } from "@/stores/session-store";
import { useUiStore } from "@/stores/ui-store";

import { ProviderStatus } from "./ProviderStatus";
import { ThemeToggle } from "./ThemeController";

const NAV = [
  { href: "/research", label: "Research", icon: FlaskConical },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/trade", label: "Trade", icon: ChartCandlestick },
  { href: "/backtest", label: "Backtest", icon: History },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

export function Sidebar({ compact }: { compact: boolean }) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const { sessions, loaded, error, reload, remove } = useSessionStore();
  const closeSheet = useUiStore((s) => s.setMobileSidebarOpen);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const onDelete = async (session: SessionListItem) => {
    const name = session.title ?? "Untitled";
    if (!window.confirm(`ลบ session "${name}"?
ข้อความ, run, provenance และ scratchpad ของ session นี้จะถูกลบถาวร`)) return;
    const err = await remove(session.id);
    setDeleteError(err);
    if (!err && pathname === `/research/${session.id}`) router.push("/research");
  };

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <nav aria-label="Main" className="flex h-full min-h-0 flex-col bg-layer-1">
      <div className={clsx("flex items-center gap-2 px-3 py-3", compact && "justify-center px-0")}>
        <span className="grid size-7 place-items-center rounded-md bg-accent text-[12px] font-bold text-bg">FH</span>
        {compact ? null : <span className="text-[14px] font-semibold">Finance Harness</span>}
      </div>
      <ul className="space-y-0.5 px-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <li key={href}>
              <Link
                href={href}
                title={label}
                onClick={() => closeSheet(false)}
                className={clsx(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px]",
                  compact && "justify-center",
                  active ? "bg-layer-2 text-fg" : "text-muted hover:bg-layer-2 hover:text-fg",
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden />
                {compact ? null : label}
              </Link>
            </li>
          );
        })}
      </ul>

      {pathname.startsWith("/trade") ? (
        <TradeHistory compact={compact} onNavigate={() => closeSheet(false)} />
      ) : pathname.startsWith("/backtest") ? (
        <BacktestHistory compact={compact} onNavigate={() => closeSheet(false)} />
      ) : (
        <>
      <div className={clsx("mt-4 flex items-center px-3", compact ? "justify-center px-0" : "justify-between")}>
        {compact ? null : <span className="text-[11px] uppercase tracking-wide text-subtle">Sessions</span>}
        <Link
          href="/research"
          title="New research session"
          data-testid="new-session"
          onClick={() => closeSheet(false)}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-accent hover:bg-layer-2"
        >
          <Plus className="size-3.5" aria-hidden />
          {compact ? null : "New"}
        </Link>
      </div>
      <ul data-testid="session-list" className="mt-1 min-h-0 flex-1 space-y-px overflow-y-auto px-2">
        {!loaded ? <li className="px-2 text-[12px] text-subtle">กำลังโหลด…</li> : null}
        {error && !compact ? <li className="px-2 text-[12px] text-error">โหลด session ไม่ได้</li> : null}
        {deleteError && !compact ? <li className="px-2 text-[12px] text-error">{deleteError}</li> : null}
        {sessions.map((s) => {
          const href = `/research/${s.id}`;
          const active = pathname === href;
          const status = s.last_run ? (s.last_run.active ? "running" : s.last_run.status) : null;
          return (
            <li key={s.id} className="group relative">
              <Link
                href={href}
                title={s.title ?? "Untitled"}
                onClick={() => closeSheet(false)}
                className={clsx(
                  "flex items-center gap-2 rounded-md px-2 py-1.5",
                  compact ? "justify-center" : "pr-8",
                  active ? "bg-layer-2" : "hover:bg-layer-2",
                )}
              >
                <StateDot state={dotFor(status)} label={status ?? "no runs"} />
                {compact ? null : (
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] text-fg">{s.title ?? "Untitled"}</span>
                    <span className="block text-[11px] text-subtle">{formatRelative(s.updated_at)}</span>
                  </span>
                )}
              </Link>
              {compact ? null : (
                <button
                  type="button"
                  data-testid="delete-session"
                  aria-label={`ลบ session ${s.title ?? "Untitled"}`}
                  title="ลบ session"
                  onClick={() => void onDelete(s)}
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
      )}

      <div className="space-y-2 border-t border-line py-2">
        <div className={clsx("px-2", compact && "flex justify-center px-0")}>
          <ThemeToggle compact={compact} />
        </div>
        <ProviderStatus compact={compact} />
      </div>
    </nav>
  );
}
