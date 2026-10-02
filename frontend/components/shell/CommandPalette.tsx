"use client";

/** Ctrl+K palette: go to a page, open a session, or search a symbol to research. */

import { clsx } from "clsx";
import { FlaskConical, MessageSquare, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type SymbolMatch } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useUiStore } from "@/stores/ui-store";

interface Item {
  id: string;
  label: string;
  hint?: string;
  kind: "page" | "session" | "symbol";
  href: string;
}

const PAGES: Item[] = [
  { id: "p-research", label: "Research", kind: "page", href: "/research" },
  { id: "p-portfolio", label: "Portfolio", kind: "page", href: "/portfolio" },
  { id: "p-trade", label: "Trade", kind: "page", href: "/trade" },
  { id: "p-settings", label: "Settings", kind: "page", href: "/settings" },
];

export function CommandPalette() {
  const open = useUiStore((s) => s.paletteOpen);
  const setOpen = useUiStore((s) => s.setPaletteOpen);
  const sessions = useSessionStore((s) => s.sessions);
  const router = useRouter();
  const [q, setQ] = useState("");
  const [symbols, setSymbols] = useState<SymbolMatch[]>([]);
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setIndex(0);
    setSymbols([]);
    requestAnimationFrame(() => input.current?.focus());
  }, [open]);

  useEffect(() => {
    const term = q.trim();
    if (!open || term.length < 1) {
      setSymbols([]);
      return;
    }
    const id = window.setTimeout(() => {
      api
        .searchSymbols(term)
        .then((r) => setSymbols(r.matches.slice(0, 5)))
        .catch(() => setSymbols([]));
    }, 250);
    return () => window.clearTimeout(id);
  }, [q, open]);

  const items = useMemo(() => {
    const term = q.trim().toLowerCase();
    const match = (s: string) => !term || s.toLowerCase().includes(term);
    return [
      ...PAGES.filter((p) => match(p.label)),
      ...sessions
        .filter((s) => match(s.title ?? ""))
        .slice(0, 8)
        .map<Item>((s) => ({ id: s.id, label: s.title ?? "Untitled", kind: "session", href: `/research/${s.id}` })),
      ...symbols.map<Item>((m) => ({
        id: `sym-${m.symbol.canonical}`,
        label: `Research ${m.symbol.display}`,
        hint: m.name,
        kind: "symbol",
        href: `/research?prefill=${encodeURIComponent(m.symbol.display)}`,
      })),
    ];
  }, [q, sessions, symbols]);

  if (!open) return null;
  const go = (item: Item | undefined) => {
    if (!item) return;
    setOpen(false);
    router.push(item.href);
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-start justify-center bg-black/50 pt-[12vh]" onClick={() => setOpen(false)}>
      <div
        role="dialog"
        aria-label="Command palette"
        className="w-[min(560px,calc(100vw-32px))] overflow-hidden rounded-lg border border-line-strong bg-layer-1 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search className="size-4 text-subtle" aria-hidden />
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
              if (e.key === "ArrowDown") setIndex((i) => Math.min(items.length - 1, i + 1));
              if (e.key === "ArrowUp") setIndex((i) => Math.max(0, i - 1));
              if (e.key === "Enter") go(items[index]);
            }}
            placeholder="ไปหน้า, เปิด session, หรือค้น symbol (เช่น PTT, AAPL)…"
            className="h-11 flex-1 bg-transparent text-[14px] outline-none placeholder:text-subtle"
          />
        </div>
        <ul className="max-h-80 overflow-y-auto py-1">
          {items.length === 0 ? <li className="px-3 py-2 text-[12.5px] text-subtle">ไม่พบ</li> : null}
          {items.map((item, i) => {
            const Icon = item.kind === "session" ? MessageSquare : item.kind === "symbol" ? Search : FlaskConical;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => go(item)}
                  className={clsx("flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px]", i === index && "bg-layer-2")}
                >
                  <Icon className="size-3.5 text-subtle" aria-hidden />
                  <span className="truncate">{item.label}</span>
                  {item.hint ? <span className="truncate text-[12px] text-subtle">{item.hint}</span> : null}
                  <span className="ml-auto text-[11px] text-subtle">{item.kind}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
