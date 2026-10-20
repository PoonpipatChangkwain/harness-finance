"use client";

/**
 * Research Composer toggle "ใช้ข้อมูลพอร์ต" (phase-13 §13.6): when on, the chosen books'
 * snapshot goes into the turn (`PostMessage.portfolio_ids`); off = `[]` (not used).
 */

import { clsx } from "clsx";
import { Briefcase, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { PortfolioBrief } from "@/lib/portfolio-api";
import { useT } from "@/lib/i18n";

export function PortfolioToggle({
  portfolios,
  selected,
  onChange,
  disabled,
}: {
  portfolios: PortfolioBrief[];
  /** chosen book ids; empty = portfolio data not used */
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  if (portfolios.length === 0) return null;
  const on = selected.length > 0;
  const toggleOn = () => onChange(on ? [] : [portfolios[0]!.id]);
  const flip = (id: string) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const label = on
    ? portfolios
        .filter((p) => selected.includes(p.id))
        .map((p) => p.name)
        .join(", ")
    : t("ใช้ข้อมูลพอร์ต", "Use portfolio data");

  return (
    <div ref={box} className="relative inline-flex items-center">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        data-testid="portfolio-toggle"
        disabled={disabled}
        onClick={toggleOn}
        title={t(
          "ใส่ข้อมูลพอร์ต (holdings, น้ำหนัก, กำไร/ขาดทุน, ความเสี่ยง) ให้ AI ใช้ในคำถามนี้",
          "Give the AI your portfolio data (holdings, weights, P/L, risk) for this question",
        )}
        className={clsx(
          "inline-flex max-w-52 items-center gap-1 rounded-l-md border px-2 py-1 text-[12.5px]",
          on ? "border-done/50 bg-done/10 text-done" : "border-line bg-layer-2 text-muted",
        )}
      >
        <Briefcase className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate">{label}</span>
      </button>
      <button
        type="button"
        aria-label={t("เลือกพอร์ต", "Choose portfolios")}
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={clsx("rounded-r-md border border-l-0 px-1 py-1", on ? "border-done/50 bg-done/10 text-done" : "border-line bg-layer-2 text-muted")}
      >
        <ChevronDown className="size-3.5" aria-hidden />
      </button>
      {open ? (
        <div data-testid="portfolio-picker" className="absolute bottom-full left-0 z-40 mb-1 w-56 rounded-md border border-line-strong bg-layer-1 p-1 shadow-lg">
          {portfolios.map((p) => (
            <label key={p.id} className="flex items-center gap-2 rounded-sm px-2 py-1 text-[12.5px] hover:bg-layer-2">
              <input type="checkbox" checked={selected.includes(p.id)} onChange={() => flip(p.id)} aria-label={p.name} />
              <span>{p.icon ?? "💼"}</span>
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              <span className="text-[11px] text-subtle">{p.positions}</span>
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}
