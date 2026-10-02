"use client";

/**
 * Symbol combobox (phase-11 §11.2): debounced (250ms) `/symbols/search`, asset badges
 * (stock·TH, stock·US, forex, crypto, commodity), keyboard navigation, and the last
 * 10 picks remembered in localStorage (storage failures just forget them).
 */

import { clsx } from "clsx";
import { Search } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { api, type SymbolInfo } from "@/lib/api";
import { assetBadge } from "@/lib/trade";

const RECENT_KEY = "fh-trade-recent-symbols";
const RECENT_MAX = 10;

export interface SymbolOption {
  symbol: SymbolInfo;
  name: string;
}

export function loadRecent(): SymbolOption[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const parsed = raw ? (JSON.parse(raw) as SymbolOption[]) : [];
    return Array.isArray(parsed) ? parsed.slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function rememberSymbol(option: SymbolOption): SymbolOption[] {
  const next = [option, ...loadRecent().filter((o) => o.symbol.canonical !== option.symbol.canonical)].slice(0, RECENT_MAX);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // storage blocked: not remembered
  }
  return next;
}

type Search = (q: string) => Promise<{ normalized: SymbolInfo | null; matches: { symbol: SymbolInfo; name: string }[] }>;

export function SymbolSearch({
  value,
  onPick,
  search = api.searchSymbols,
  autoFocus = false,
}: {
  value?: SymbolInfo | null;
  onPick: (option: SymbolOption) => void;
  search?: Search;
  autoFocus?: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<SymbolOption[]>([]);
  const [recent, setRecent] = useState<SymbolOption[]>([]);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => setRecent(loadRecent()), []);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setOptions([]);
      return;
    }
    let alive = true;
    const t = window.setTimeout(() => {
      setLoading(true);
      search(term)
        .then((r) => {
          if (!alive) return;
          const seen = new Set<string>();
          const out: SymbolOption[] = [];
          if (r.normalized) {
            out.push({ symbol: r.normalized, name: r.normalized.display });
            seen.add(r.normalized.canonical);
          }
          for (const m of r.matches) {
            if (seen.has(m.symbol.canonical)) continue;
            seen.add(m.symbol.canonical);
            out.push({ symbol: m.symbol, name: m.name });
          }
          setOptions(out);
          setActive(0);
        })
        .catch(() => alive && setOptions([]))
        .finally(() => alive && setLoading(false));
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [q, search]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const shown = q.trim() ? options : recent;
  const pick = (o: SymbolOption) => {
    setRecent(rememberSymbol(o));
    setQ("");
    setOpen(false);
    onPick(o);
  };

  return (
    <div ref={box} className="relative w-full max-w-sm">
      <div className="flex items-center gap-2 rounded-md border border-line bg-bg px-2 py-1.5 focus-within:border-accent">
        <Search className="size-4 text-subtle" aria-hidden />
        <input
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-label="ค้นหา symbol"
          data-testid="symbol-search"
          autoFocus={autoFocus}
          value={q}
          placeholder={value ? `${value.display} · ${value.canonical}` : "ค้นหา symbol เช่น PTT, AAPL, EURUSD"}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, shown.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter" && shown[active]) {
              e.preventDefault();
              pick(shown[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-[13px] text-fg outline-none placeholder:text-subtle"
        />
        {value ? <AssetBadge symbol={value} /> : null}
      </div>
      {open && (shown.length || loading) ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-40 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-line-strong bg-layer-1 py-1 shadow-lg"
        >
          {!q.trim() ? <li className="px-2 py-0.5 text-[11px] uppercase text-subtle">ล่าสุด</li> : null}
          {loading && !shown.length ? <li className="px-2 py-1 text-[12.5px] text-subtle">กำลังค้นหา…</li> : null}
          {shown.map((o, i) => (
            <li
              key={o.symbol.canonical}
              role="option"
              aria-selected={i === active}
              data-testid="symbol-option"
              onMouseDown={(e) => {
                e.preventDefault();
                pick(o);
              }}
              onMouseEnter={() => setActive(i)}
              className={clsx("flex cursor-pointer items-center gap-2 px-2 py-1 text-[13px]", i === active && "bg-layer-2")}
            >
              <span className="num font-medium">{o.symbol.canonical}</span>
              <span className="min-w-0 flex-1 truncate text-muted">{o.name}</span>
              <AssetBadge symbol={o.symbol} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function AssetBadge({ symbol }: { symbol: Pick<SymbolInfo, "asset_type" | "market"> }) {
  return (
    <span data-testid="asset-badge" className="shrink-0 rounded-sm bg-layer-2 px-1.5 py-0.5 text-[11px] text-muted">
      {assetBadge(symbol)}
    </span>
  );
}
