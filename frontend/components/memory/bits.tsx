/** Small shared pieces of the /memory page (P17). */

import { clsx } from "clsx";
import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { intlLocale, useT } from "@/lib/i18n";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleDateString(intlLocale(), { day: "numeric", month: "short", year: "2-digit" });
}

/** A 0–1 value as a thin bar + percent (confidence, success rate). */
export function RatioBar({ value, label, tone }: { value: number; label: string; tone?: "auto" | "accent" }) {
  const v = Math.min(1, Math.max(0, value));
  const color = tone === "accent" ? "bg-accent" : v >= 0.6 ? "bg-done" : v >= 0.4 ? "bg-warning" : "bg-error";
  return (
    <span className="inline-flex items-center gap-1.5" title={`${label} ${Math.round(v * 100)}%`}>
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-layer-2" role="meter" aria-label={label} aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100}>
        <span className={clsx("block h-full rounded-full", color)} style={{ width: `${v * 100}%` }} />
      </span>
      <span className="num text-[11px] text-muted">{Math.round(v * 100)}%</span>
    </span>
  );
}

export function Pill({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center rounded-sm px-1.5 py-px text-[11px]", className ?? "bg-layer-2 text-muted")}>
      {children}
    </span>
  );
}

/** Right-hand drawer (full width on phones); Esc closes. */
export function Drawer({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={label}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-md flex-col border-l border-line bg-layer-1 shadow-xl"
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-2">
          <h2 className="min-w-0 flex-1 truncate text-[14px] font-semibold">{label}</h2>
          <button type="button" aria-label={t("ปิด", "Close")} onClick={onClose} className="rounded-md p-1 text-subtle hover:bg-layer-2 hover:text-fg">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 text-[12.5px]">{children}</div>
      </aside>
    </div>
  );
}
