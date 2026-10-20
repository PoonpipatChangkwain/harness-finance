"use client";

/** Create / rename / delete a book (phase-13 §13.6): name, icon, color, base currency. */

import { clsx } from "clsx";
import { Trash2, X } from "lucide-react";
import { useState } from "react";

import type { PortfolioBrief } from "@/lib/portfolio-api";

import { PALETTE } from "./AllocationChart";
import { tr, useT } from "@/lib/i18n";

export interface PortfolioMeta {
  name: string;
  icon: string | null;
  color: string | null;
  base_currency: string;
}

const ICONS = ["💼", "🇹🇭", "🇺🇸", "🚀", "🏦", "🌱", "💰", "📈"];

export function PortfolioFormDialog({
  existing,
  onSubmit,
  onDelete,
  onClose,
}: {
  existing?: PortfolioBrief | null;
  onSubmit: (meta: PortfolioMeta) => Promise<void>;
  onDelete?: () => Promise<void>;
  onClose: () => void;
}) {
  const t = useT();
  const [name, setName] = useState(existing?.name ?? "");
  const [icon, setIcon] = useState<string | null>(existing?.icon ?? "💼");
  const [color, setColor] = useState<string | null>(existing?.color ?? PALETTE[0]!);
  const [ccy, setCcy] = useState(existing?.base_currency ?? "THB");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" aria-label="Portfolio" className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4">
      <form
        data-testid="portfolio-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return setError(tr("ตั้งชื่อพอร์ต", "Name the portfolio"));
          void run(() => onSubmit({ name: name.trim(), icon, color, base_currency: ccy }));
        }}
        className="w-full max-w-sm rounded-lg border border-line-strong bg-layer-1 shadow-xl"
      >
        <div className="flex items-center border-b border-line px-4 py-2">
          <h2 className="text-[14px] font-semibold">{existing ? t("แก้ไขพอร์ต", "Edit portfolio") : t("สร้างพอร์ตใหม่", "New portfolio")}</h2>
          <span className="flex-1" />
          <button type="button" aria-label={t("ปิด", "Close")} onClick={onClose} className="rounded-md p-1 text-muted hover:bg-layer-2">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="space-y-3 p-4 text-[12.5px]">
          <label className="block text-muted">
            {t("ชื่อพอร์ต", "Portfolio name")}
            <input
              autoFocus
              aria-label="portfolio name"
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              className="mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg outline-none focus:border-accent"
            />
          </label>
          <div>
            <div className="text-muted">{t("ไอคอน", "Icon")}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {ICONS.map((i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setIcon(i)}
                  className={clsx("grid size-7 place-items-center rounded-md text-[15px]", icon === i ? "bg-accent-muted ring-1 ring-accent" : "bg-layer-2")}
                >
                  {i}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="text-muted">{t("สี", "Color")}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {PALETTE.map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-label={`color ${c}`}
                  onClick={() => setColor(c)}
                  className={clsx("size-6 rounded-full", color === c && "ring-2 ring-fg ring-offset-2 ring-offset-layer-1")}
                  style={{ background: c }}
                />
              ))}
            </div>
          </div>
          <label className="block text-muted">
            {t("สกุลเงินหลัก", "Base currency")}
            <select
              aria-label="base currency"
              value={ccy}
              disabled={!!existing}
              onChange={(e) => setCcy(e.target.value)}
              className="mt-0.5 block rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg"
            >
              <option value="THB">THB</option>
              <option value="USD">USD</option>
            </select>
            {existing ? <span className="text-[11px] text-subtle">{t("เปลี่ยนสกุลเงินของพอร์ตที่มีอยู่ไม่ได้", "The currency of an existing portfolio cannot be changed")}</span> : null}
          </label>
          {error ? <p className="text-error">{error}</p> : null}
        </div>
        <div className="flex items-center gap-2 border-t border-line px-4 py-2">
          {existing && onDelete ? (
            <button
              type="button"
              data-testid="delete-portfolio"
              disabled={busy}
              onClick={() => {
                const message = t(
                  `ลบพอร์ต "${existing.name}"? positions, revisions และแผนของพอร์ตนี้จะถูกลบถาวร`,
                  `Delete portfolio "${existing.name}"? Its positions, revisions and plans will be permanently deleted.`,
                );
                if (window.confirm(message)) void run(onDelete);
              }}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px] text-error hover:bg-error/15"
            >
              <Trash2 className="size-3.5" aria-hidden /> {t("ลบพอร์ต", "Delete portfolio")}
            </button>
          ) : null}
          <span className="flex-1" />
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
            {t("ยกเลิก", "Cancel")}
          </button>
          <button type="submit" disabled={busy} className="rounded-md bg-accent px-3 py-1 text-[13px] text-bg disabled:opacity-50">
            {existing ? t("บันทึก", "Save") : t("สร้าง", "Create")}
          </button>
        </div>
      </form>
    </div>
  );
}
