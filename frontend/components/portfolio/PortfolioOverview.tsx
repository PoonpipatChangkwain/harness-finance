"use client";

/**
 * Every book at once (phase-13 §13.6, modelled on Dime! "สินทรัพย์ของฉัน"): a value card in
 * the display currency with the ≈ other currency and the FX rate, cost basis, day change and
 * unrealized gain; a donut by portfolio; and the list by asset (same symbol merged across
 * books) or by portfolio, filtered by market and sortable by value or gain. All numbers come
 * from `GET /portfolios/summary` (computed in the backend, one provenance record).
 */

import { clsx } from "clsx";
import { useEffect, useMemo, useState } from "react";

import { formatMoney, formatPct, formatSigned, MARKET_LABEL, upDownClass } from "@/lib/portfolio-format";
import { portfolioApi, type MarketFilter, type PortfolioSummary, type SummaryAsset } from "@/lib/portfolio-api";

import { CASH_COLOR, Donut, PALETTE } from "./AllocationChart";
import { ProvValue } from "./ProvValue";
import { useT } from "@/lib/i18n";

type Mode = "assets" | "portfolios";
type SortKey = "value" | "pnl";

export function sortAssets(assets: SummaryAsset[], key: SortKey): SummaryAsset[] {
  const v = (a: SummaryAsset) => (key === "value" ? a.value : (a.unrealized_pnl_pct ?? Number.NEGATIVE_INFINITY));
  return [...assets].sort((a, b) => v(b) - v(a));
}

function Chip({ active, onClick, children, testId }: { active: boolean; onClick: () => void; children: React.ReactNode; testId?: string }) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={active}
      onClick={onClick}
      className={clsx("rounded-full border px-3 py-0.5 text-[12.5px]", active ? "border-accent bg-accent-muted text-accent" : "border-line text-muted hover:text-fg")}
    >
      {children}
    </button>
  );
}

function Initials({ symbol, color }: { symbol: string; color: string }) {
  return (
    <span className="grid size-8 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white" style={{ background: color }}>
      {symbol.replace(/\..*$/, "").replace(/=X$/, "").slice(0, 4)}
    </span>
  );
}

export function PortfolioOverview({
  revision,
  onSelect,
  load = portfolioApi.summary,
}: {
  revision: number;
  onSelect: (id: string) => void;
  load?: typeof portfolioApi.summary;
}) {
  const t = useT();
  const [currency, setCurrency] = useState<"THB" | "USD">("USD");
  const [market, setMarket] = useState<MarketFilter>("ALL");
  const [mode, setMode] = useState<Mode>("assets");
  const [sort, setSort] = useState<SortKey>("value");
  const [data, setData] = useState<PortfolioSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alt = currency === "USD" ? "THB" : "USD";

  useEffect(() => {
    let alive = true;
    load({ currency, market, alt_currency: alt })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [currency, market, alt, revision, load]);

  const altAssets = useMemo(() => new Map((data?.alt?.assets ?? []).map((a) => [a.symbol, a.value])), [data]);
  const colors = useMemo(() => {
    const m = new Map<string, string>();
    (data?.portfolios ?? []).forEach((p, i) => m.set(p.id, p.color ?? PALETTE[i % PALETTE.length]!));
    return m;
  }, [data]);

  if (error) return <p className="text-[12.5px] text-error">{t("โหลดภาพรวมไม่ได้", "Could not load the overview")}: {error}</p>;
  if (!data) return <div className="h-40 animate-pulse rounded-md bg-layer-1" />;

  const pid = data.provenance_id;
  const fxRates = Object.entries(data.alt?.fx ?? {}).filter(([base]) => base !== alt);
  const assets = sortAssets(data.assets, sort);
  const slices = [
    ...data.portfolios.filter((p) => (p.weight ?? 0) > 0).map((p) => ({ name: p.name, value: p.weight ?? 0, color: colors.get(p.id)! })),
  ];
  if (market === "ALL" && data.cash > 0 && data.total_value > 0 && slices.length === 0) {
    slices.push({ name: t("เงินสด", "Cash"), value: 1, color: CASH_COLOR });
  }

  return (
    <div data-testid="portfolio-overview" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" className="flex rounded-md bg-layer-2 p-0.5 text-[12.5px]">
          {(["assets", "portfolios"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={clsx("rounded-sm px-3 py-0.5", mode === m ? "bg-layer-1 text-fg" : "text-muted")}
            >
              {m === "assets" ? t("แยกสินทรัพย์", "By asset") : t("แยกพอร์ต", "By portfolio")}
            </button>
          ))}
        </div>
        <span className="flex-1" />
        {(["ALL", "TH", "US"] as MarketFilter[]).map((m) => (
          <Chip key={m} testId={`market-${m}`} active={market === m} onClick={() => setMarket(m)}>
            {m === "ALL" ? t("ทั้งหมด", "All") : MARKET_LABEL[m]}
          </Chip>
        ))}
        <select
          aria-label="display currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value as "THB" | "USD")}
          className="rounded-md border border-line bg-layer-2 px-1.5 py-0.5 text-[12.5px]"
        >
          <option value="USD">USD</option>
          <option value="THB">THB</option>
        </select>
      </div>

      <section data-testid="overview-card" className="flex flex-wrap items-center gap-6 rounded-lg border border-line bg-layer-1 p-4">
        <div className="min-w-56 flex-1 space-y-1">
          <div className="text-[12px] text-muted">
            {t("มูลค่ารวม", "Total value")}
            {market !== "ALL" ? ` · ${MARKET_LABEL[market]}` : ""}
          </div>
          <div className="text-[24px] font-semibold">
            <ProvValue value={data.total_value} provenanceId={pid} /> <span className="text-[13px] font-normal text-subtle">{currency}</span>
          </div>
          {data.alt ? (
            <div className="num text-[13px] text-muted">
              ≈ {formatMoney(data.alt.total_value)} {alt}
              {fxRates.map(([base, r]) => (
                <span key={base} className="ml-2 text-[11px] text-subtle">
                  (1 {base} = {r.rate.toFixed(4)} {alt})
                </span>
              ))}
            </div>
          ) : null}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 pt-2 text-[12.5px] sm:grid-cols-3">
            <div>
              <dt className="text-muted">{t("ต้นทุนรวม", "Total cost")}</dt>
              <dd>
                <ProvValue value={data.cost_basis} provenanceId={pid} />
              </dd>
            </div>
            <div>
              <dt className="text-muted">{t("เปลี่ยนจากวันก่อน", "Change since yesterday")}</dt>
              <dd>
                <ProvValue value={data.day_pnl_pct} provenanceId={pid} format="signedPct" colored />{" "}
                <span className={clsx("num text-[11.5px]", upDownClass(data.day_pnl))}>({formatSigned(data.day_pnl)})</span>
              </dd>
            </div>
            <div>
              <dt className="text-muted">{t("กำไรที่ถืออยู่", "Unrealized P/L")}</dt>
              <dd>
                <ProvValue value={data.unrealized_pnl} provenanceId={pid} format="signed" colored />{" "}
                <span className={clsx("num text-[11.5px]", upDownClass(data.unrealized_pnl_pct))}>({formatPct(data.unrealized_pnl_pct, 2, true)})</span>
              </dd>
            </div>
          </dl>
        </div>
        {slices.length ? (
          <div className="flex items-center gap-3">
            <Donut slices={slices} size={140} />
            <ul className="space-y-0.5 text-[12px]">
              {slices.map((s) => (
                <li key={s.name}>
                  <span className="mr-1.5 inline-block size-2.5 rounded-sm align-middle" style={{ background: s.color }} />
                  {s.name} <span className="num text-muted">{formatPct(s.value, 1)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {data.warnings.length ? (
        <div className="rounded-md bg-warning/10 px-3 py-1.5 text-[12px] text-warning">{data.warnings.join(" · ")}</div>
      ) : null}

      {mode === "portfolios" ? (
        <ul data-testid="overview-portfolios" className="divide-y divide-line rounded-md border border-line bg-layer-1">
          {data.portfolios.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => onSelect(p.id)} className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-layer-2">
                <span className="grid size-8 place-items-center rounded-full text-[15px]" style={{ background: `${colors.get(p.id)}33` }}>
                  {p.icon ?? "💼"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-medium">{p.name}</span>
                  <span className="block text-[11.5px] text-subtle">
                    {p.holdings ?? 0} {t("สินทรัพย์", "assets")} · {p.base_currency}
                  </span>
                </span>
                <span className="text-right">
                  <span className="num block text-[13.5px]">
                    {formatMoney(p.value)} <span className="text-[11px] text-subtle">{currency}</span>
                  </span>
                  <span className={clsx("num block text-[11.5px]", upDownClass(p.unrealized_pnl))}>
                    {formatSigned(p.unrealized_pnl)} ({formatPct(p.unrealized_pnl_pct, 2, true)})
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <section className="rounded-md border border-line bg-layer-1">
          <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[12px] text-muted">
            {assets.length} {t("สินทรัพย์", "assets")}
            <span className="flex-1" />
            {t("เรียงตาม", "Sort by")}
            <Chip active={sort === "value"} onClick={() => setSort("value")} testId="sort-value">
              {t("มูลค่า", "Value")}
            </Chip>
            <Chip active={sort === "pnl"} onClick={() => setSort("pnl")} testId="sort-pnl">
              {t("กำไร", "P/L")}
            </Chip>
          </div>
          <ul data-testid="overview-assets" className="divide-y divide-line">
            {assets.map((a, i) => (
              <li key={a.symbol} data-testid="overview-asset" className="flex items-center gap-3 px-3 py-2">
                <Initials symbol={a.display} color={PALETTE[i % PALETTE.length]!} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-medium">{a.display}</span>
                  <span className="block text-[11.5px] text-subtle">
                    {formatPct(a.weight, 2)} · {MARKET_LABEL[a.market] ?? a.market} · {a.portfolios.length} {t("พอร์ต", "portfolio(s)")}
                  </span>
                </span>
                <span className="text-right">
                  <span className="num block text-[13.5px]">
                    {formatMoney(a.value)} <span className="text-[11px] text-subtle">{currency}</span>
                  </span>
                  {altAssets.has(a.symbol) ? (
                    <span className="num block text-[11px] text-subtle">
                      ≈ {formatMoney(altAssets.get(a.symbol))} {alt}
                    </span>
                  ) : null}
                </span>
                <span className={clsx("num w-28 text-right text-[12.5px]", upDownClass(a.unrealized_pnl))}>
                  {formatPct(a.unrealized_pnl_pct, 2, true)}
                  <span className="block text-[11px]">{formatSigned(a.unrealized_pnl)}</span>
                </span>
              </li>
            ))}
            {assets.length === 0 ? <li className="px-3 py-4 text-center text-[12.5px] text-subtle">{t("ไม่มีสินทรัพย์ในตลาดนี้", "No assets in this market")}</li> : null}
          </ul>
        </section>
      )}
    </div>
  );
}
