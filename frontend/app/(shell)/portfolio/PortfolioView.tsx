"use client";

/**
 * Portfolio page (ui-spec §6, phase-13 §13.3 + §13.6).
 * - chips: ทั้งหมด (cross-portfolio overview, Dime!-style) · one chip per book · + new book
 * - a book: summary tiles · holdings (guided add dialog / paste from a broker app / inline
 *   edit / CSV / %-only) · allocation · sector
 *   exposure · risk · rebalance proposal of the advisor · plans · weekly AI reports
 * - AI Advisor lives in the inspector; when its run reaches approval the page scrolls to
 *   the proposal and highlights it
 * Every number comes from the backend analytics (refreshed after a save and every 60 s).
 */

import { clsx } from "clsx";
import { ClipboardPaste, FileUp, Percent, Plus, Settings2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AddPositionDialog } from "@/components/portfolio/AddPositionDialog";
import { AllocationChart } from "@/components/portfolio/AllocationChart";
import { ExposureBars } from "@/components/portfolio/ExposureBars";
import { HoldingsTable } from "@/components/portfolio/HoldingsTable";
import { ImportCsvDialog } from "@/components/portfolio/ImportCsvDialog";
import { PasteHoldingsDialog } from "@/components/portfolio/PasteHoldingsDialog";
import { PlansHistory } from "@/components/portfolio/PlansHistory";
import { PORTFOLIO_TABS } from "@/components/portfolio/PortfolioInspectorTabs";
import { PortfolioFormDialog, type PortfolioMeta } from "@/components/portfolio/PortfolioFormDialog";
import { PortfolioOverview } from "@/components/portfolio/PortfolioOverview";
import { ProposalTable, type PlanApproval } from "@/components/portfolio/ProposalTable";
import { RiskPanel } from "@/components/portfolio/RiskPanel";
import { SummaryTiles } from "@/components/portfolio/SummaryTiles";
import { WeeklyReports } from "@/components/portfolio/WeeklyReports";
import { useInspectorTabs } from "@/components/shell/InspectorRegistry";
import { StateDot, dotFor } from "@/components/ui/StateDot";
import { api, ApiError } from "@/lib/api";
import { buildPositions, mergePositions, remapErrors, rowsFromBook, type EntryMode } from "@/lib/holdings";
import {
  entryErrors,
  portfolioApi,
  type AdvisorRun,
  type EntryError,
  type PortfolioAnalytics,
  type PortfolioBrief,
  type PortfolioOut,
  type PositionInput,
} from "@/lib/portfolio-api";
import { settingsApi } from "@/lib/settings-api";
import { isTerminal } from "@/stores/run-reducer";
import { useRun } from "@/stores/run-store";
import { ALL, defaultSelection, usePortfolioStore } from "@/stores/portfolio-store";
import { useUiStore } from "@/stores/ui-store";

const REFRESH_MS = 60_000;
const OPEN_RUN = new Set(["pending", "running", "waiting_approval", "interrupted"]);
const OUTCOME: Record<string, string> = {
  approved: "อนุมัติ · บันทึกแผนแล้ว",
  declined: "ไม่อนุมัติ",
  rejected: "ผิด policy / แก้แล้วไม่ผ่าน",
  no_change: "AI เห็นว่าไม่ต้องปรับ",
  no_proposal: "ไม่มีข้อเสนอที่ใช้ได้",
  unavailable: "คำนวณไม่ได้",
  unvalued: "พอร์ตแบบ % อย่างเดียว",
  not_provided: "ยังไม่มีพอร์ต",
  empty: "พอร์ตว่าง",
};

function useSectorLimit(): { limit: number; label: string } {
  const [state, setState] = useState({ limit: 0.4, label: "advisor · balanced" });
  useEffect(() => {
    settingsApi
      .get()
      .then((s) => {
        const v = s.values.policy.max_sector_weight;
        if (v !== null) setState({ limit: v, label: "Settings › Policy" });
      })
      .catch(() => {});
  }, []);
  return state;
}

function EmptyState({
  onAdd,
  onPaste,
  onPercent,
  onImport,
}: {
  onAdd: () => void;
  onPaste: () => void;
  onPercent: () => void;
  onImport: () => void;
}) {
  return (
    <div data-testid="portfolio-empty" className="mx-auto flex max-w-lg flex-col items-center gap-3 py-12 text-center">
      <h2 className="text-[18px] font-semibold">ยังไม่มีข้อมูลพอร์ต</h2>
      <p className="text-[13px] text-muted">
        กรอก position ของคุณ แล้วระบบจะดึงราคา คำนวณน้ำหนัก กำไร/ขาดทุน และความเสี่ยงให้ — ทุกตัวเลขตรวจที่มาได้ (ไม่มีการส่งคำสั่งซื้อขายจริง)
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        <button type="button" data-testid="empty-add" onClick={onAdd} className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-[13px] text-bg">
          <Plus className="size-4" aria-hidden /> เพิ่ม position
        </button>
        <button type="button" data-testid="empty-paste" onClick={onPaste} className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1.5 text-[13px]">
          <ClipboardPaste className="size-4" aria-hidden /> วางข้อความจากแอปโบรกเกอร์
        </button>
        <button type="button" data-testid="empty-import" onClick={onImport} className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1.5 text-[13px]">
          <FileUp className="size-4" aria-hidden /> Import CSV
        </button>
        <button type="button" data-testid="empty-percent" onClick={onPercent} className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1.5 text-[13px]">
          <Percent className="size-4" aria-hidden /> กรอกแบบ %
        </button>
      </div>
    </div>
  );
}

function BookDetail({ id, brief }: { id: string; brief: PortfolioBrief | null }) {
  const revision = usePortfolioStore((s) => s.revision);
  const bump = usePortfolioStore((s) => s.bump);
  const reloadList = usePortfolioStore((s) => s.reload);
  const runId = usePortfolioStore((s) => s.advisorRun[id] ?? null);
  const setAdvisorRun = usePortfolioStore((s) => s.setAdvisorRun);
  const proposalFocus = usePortfolioStore((s) => s.proposalFocus);
  const selectRun = useUiStore((s) => s.selectRun);
  const sector = useSectorLimit();

  const [book, setBook] = useState<PortfolioOut | null | undefined>(undefined);
  const [bookError, setBookError] = useState<string | null>(null);
  const [analytics, setAnalytics] = useState<PortfolioAnalytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [loadingAnalytics, setLoadingAnalytics] = useState(false);
  const [startWith, setStartWith] = useState<{ kind: EntryMode; nonce: number } | null>(null);
  const [importing, setImporting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [runs, setRuns] = useState<AdvisorRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(false);
  const proposalRef = useRef<HTMLDivElement>(null);

  const loadBook = useCallback(async () => {
    try {
      setBook(await portfolioApi.get(id));
      setBookError(null);
    } catch (err) {
      // 404 = never saved ("not provided"), shown as the empty state — never as an empty book
      if (err instanceof ApiError && err.status === 404) setBook(null);
      else setBookError(err instanceof Error ? err.message : String(err));
    }
  }, [id]);

  const loadAnalytics = useCallback(async () => {
    setLoadingAnalytics(true);
    try {
      setAnalytics(await portfolioApi.analytics(id));
      setAnalyticsError(null);
    } catch (err) {
      setAnalyticsError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingAnalytics(false);
    }
  }, [id]);

  const loadRuns = useCallback(() => {
    portfolioApi
      .advisorRuns(id)
      .then((r) => setRuns(r.runs))
      .catch(() => setRuns([]));
  }, [id]);

  useEffect(() => {
    setBook(undefined);
    setAnalytics(null);
    setStartWith(null);
  }, [id]);
  useEffect(() => {
    void loadBook();
    loadRuns();
  }, [loadBook, loadRuns, revision]);

  const hasPositions = !!book && book.portfolio.positions.length > 0;
  useEffect(() => {
    if (!hasPositions) return;
    void loadAnalytics();
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadAnalytics();
    }, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [hasPositions, loadAnalytics, book?.portfolio_hash]);

  // follow the newest open advisor run of this book (e.g. after a reload)
  useEffect(() => {
    if (runId) return;
    const open = runs.find((r) => OPEN_RUN.has(r.status)) ?? runs[0];
    if (open) setAdvisorRun(id, open.run_id);
  }, [runs, runId, id, setAdvisorRun]);

  const view = useRun(runId);
  const pending = view ? Object.values(view.approvals).find((a) => a.outcome === "pending" && a.kind === "rebalance_plan") : undefined;
  const lastRun = runs.find((r) => r.run_id === runId) ?? null;

  const scrollToProposal = useCallback(() => {
    proposalRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    setHighlight(true);
    window.setTimeout(() => setHighlight(false), 3000);
  }, []);
  useEffect(() => {
    if (pending?.approvalId) scrollToProposal();
  }, [pending?.approvalId, scrollToProposal]);
  useEffect(() => {
    if (proposalFocus) scrollToProposal();
  }, [proposalFocus, scrollToProposal]);

  const runDone = view ? isTerminal(view.status) : false;
  useEffect(() => {
    if (!runDone) return;
    loadRuns();
    bump();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per finished run
  }, [runDone, runId]);

  const save = async (positions: PositionInput[], cash: string | null): Promise<EntryError[] | null> => {
    try {
      const out = await portfolioApi.put(id, { positions, cash, ...(book ? {} : { name: brief?.name ?? "พอร์ตหลัก" }) });
      setBook(out);
      setStartWith(null);
      bump();
      void reloadList();
      return null;
    } catch (err) {
      return entryErrors(err) ?? [{ index: null, symbol: null, field: null, message: err instanceof Error ? err.message : String(err) }];
    }
  };

  /** rows of the add / paste dialogs → merged into the saved book (or replacing it) → PUT; 422 indexed by the new rows */
  const saveNew = async (rows: PositionInput[], replace = false): Promise<EntryError[] | null> => {
    const existing = replace || !book ? [] : buildPositions(rowsFromBook(book.portfolio));
    const merged = mergePositions(existing, rows);
    const result = await save(merged.positions, book?.portfolio.cash ?? null);
    return result ? remapErrors(result, merged.indexOf) : null;
  };

  const resolve = async (answer: PlanApproval) => {
    if (!pending) return;
    setBusy(true);
    setApprovalError(null);
    try {
      await api.resolveApproval(pending.approvalId, answer);
    } catch (err) {
      setApprovalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const names = useMemo(() => new Map([[id, brief?.name ?? id]]), [id, brief?.name]);

  if (book === undefined) {
    return bookError ? (
      <p className="text-[12.5px] text-error">โหลดพอร์ตไม่ได้: {bookError}</p>
    ) : (
      <div className="h-48 animate-pulse rounded-md bg-layer-1" />
    );
  }

  const showTable = hasPositions || startWith !== null;
  return (
    <div className="space-y-3">
      {!showTable ? (
        <EmptyState
          onAdd={() => setAdding(true)}
          onPaste={() => setPasting(true)}
          onPercent={() => setStartWith({ kind: "weight", nonce: Date.now() })}
          onImport={() => setImporting(true)}
        />
      ) : null}

      {hasPositions ? (
        analytics ? (
          <>
            {analytics.warnings.length ? (
              <ul data-testid="analytics-warnings" className="space-y-0.5 rounded-md bg-warning/10 px-3 py-1.5 text-[12px] text-warning">
                {analytics.warnings.map((w, i) => (
                  <li key={`${w.code}-${i}`}>⚠ {w.message}</li>
                ))}
              </ul>
            ) : null}
            <SummaryTiles analytics={analytics} />
          </>
        ) : analyticsError ? (
          <p className="rounded-md bg-error/10 px-3 py-1.5 text-[12.5px] text-error">คำนวณ analytics ไม่ได้: {analyticsError}</p>
        ) : (
          <div data-testid="analytics-skeleton" className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="h-16 animate-pulse rounded-md bg-layer-1" />
            ))}
          </div>
        )
      ) : null}

      {showTable ? (
        <HoldingsTable
          book={book?.portfolio ?? null}
          analytics={analytics}
          onSave={save}
          onImport={() => setImporting(true)}
          onPaste={() => setPasting(true)}
          startWith={startWith}
          portfolioId={id}
        />
      ) : null}
      {loadingAnalytics && analytics ? <p className="text-[11px] text-subtle">กำลังอัปเดตราคา…</p> : null}

      {hasPositions && analytics && !analytics.weights_declared_only ? (
        <>
          <div className="grid gap-3 xl:grid-cols-2">
            <AllocationChart analytics={analytics} />
            <ExposureBars analytics={analytics} limit={sector.limit} limitLabel={sector.label} />
          </div>
          <RiskPanel analytics={analytics} />
        </>
      ) : hasPositions && analytics ? (
        <div className="grid gap-3 xl:grid-cols-2">
          <AllocationChart analytics={analytics} />
          <RiskPanel analytics={analytics} />
        </div>
      ) : null}

      <div ref={proposalRef} className="scroll-mt-4">
        {pending ? (
          <>
            {approvalError ? <p className="mb-1 text-[12px] text-error">{approvalError}</p> : null}
            <ProposalTable approval={pending} onResolve={(a) => void resolve(a)} busy={busy} highlight={highlight} />
          </>
        ) : view && !runDone ? (
          <div data-testid="advisor-running" className="flex items-center gap-2 rounded-md border border-line bg-layer-1 px-3 py-2 text-[12.5px]">
            <StateDot state="ongoing" label="running" />
            AI Advisor กำลังวิเคราะห์ — ดูขั้นตอนได้ใน inspector
            <button type="button" onClick={() => runId && selectRun(runId)} className="ml-auto text-accent">
              ดู process
            </button>
          </div>
        ) : lastRun?.finished_at ? (
          <details data-testid="advisor-result" className="rounded-md border border-line bg-layer-1 px-3 py-2 text-[12.5px]">
            <summary className="flex cursor-pointer items-center gap-2">
              <StateDot state={dotFor(lastRun.status)} label={lastRun.status} />
              ผล advisor ล่าสุด: <b>{OUTCOME[lastRun.outcome ?? ""] ?? lastRun.outcome ?? lastRun.status}</b>
              {lastRun.plan_id ? <span className="num text-subtle">plan {lastRun.plan_id.slice(-8)}</span> : null}
            </summary>
            {lastRun.report_md ? (
              <div className="fh-markdown mt-2 text-[13px]">
                <Markdown remarkPlugins={[remarkGfm]}>{lastRun.report_md}</Markdown>
              </div>
            ) : null}
          </details>
        ) : null}
      </div>

      {book ? <PlansHistory portfolioId={id} revision={revision} onApplied={bump} /> : null}
      {book ? <WeeklyReports portfolioId={id} names={names} revision={revision} /> : null}

      {adding ? (
        <AddPositionDialog
          portfolioId={id}
          onClose={() => setAdding(false)}
          onSave={(rows) => saveNew(rows)}
          onPercentMode={() => {
            setAdding(false);
            setStartWith({ kind: "weight", nonce: Date.now() });
          }}
        />
      ) : null}
      {pasting ? (
        <PasteHoldingsDialog
          portfolioId={id}
          hasPositions={hasPositions}
          onClose={() => setPasting(false)}
          onSave={(rows, replace) => saveNew(rows, replace)}
        />
      ) : null}
      {importing ? (
        <ImportCsvDialog
          portfolioId={id}
          onClose={() => setImporting(false)}
          onCommitted={() => {
            bump();
            void reloadList();
          }}
        />
      ) : null}
    </div>
  );
}

export function PortfolioView() {
  useInspectorTabs(PORTFOLIO_TABS);
  const router = useRouter();
  const search = useSearchParams();
  const { portfolios, loaded, error, reload, selectedId, select, revision, bump } = usePortfolioStore();
  const inspectorTab = useUiStore((s) => s.inspectorTab);
  const setInspectorTab = useUiStore((s) => s.setInspectorTab);
  const [form, setForm] = useState<"create" | "edit" | null>(null);

  useEffect(() => {
    void reload();
  }, [reload]);
  useEffect(() => {
    if (!PORTFOLIO_TABS.some((t) => t.id === inspectorTab)) setInspectorTab("advisor");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the page opens
  }, []);

  const wanted = search?.get("pf") ?? null;
  useEffect(() => {
    if (!loaded) return;
    select(wanted ?? defaultSelection(portfolios));
  }, [loaded, wanted, portfolios, select]);

  const go = (id: string) => router.replace(`/portfolio?pf=${encodeURIComponent(id)}`);
  const brief = portfolios.find((p) => p.id === selectedId) ?? null;
  const names = useMemo(() => new Map(portfolios.map((p) => [p.id, p.name])), [portfolios]);

  const create = async (meta: PortfolioMeta) => {
    const out = await portfolioApi.create({ ...meta, positions: [] });
    await reload();
    go(out.portfolio.id);
  };
  const edit = async (meta: PortfolioMeta) => {
    if (!brief) return;
    await portfolioApi.patch(brief.id, { name: meta.name, icon: meta.icon, color: meta.color });
    await reload();
    bump();
  };
  const remove = async () => {
    if (!brief) return;
    await portfolioApi.remove(brief.id);
    await reload();
    go(ALL);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 pr-12">
        <h1 className="mr-2 text-[15px] font-semibold">Portfolio</h1>
        <nav aria-label="Portfolios" className="flex flex-wrap items-center gap-1.5">
          {portfolios.length > 0 ? (
            <button
              type="button"
              data-testid="pf-chip-all"
              aria-pressed={selectedId === ALL}
              onClick={() => go(ALL)}
              className={clsx("rounded-full border px-3 py-0.5 text-[12.5px]", selectedId === ALL ? "border-accent bg-accent-muted text-accent" : "border-line text-muted hover:text-fg")}
            >
              ทั้งหมด
            </button>
          ) : null}
          {portfolios.map((p) => (
            <button
              key={p.id}
              type="button"
              data-testid="pf-chip"
              aria-pressed={selectedId === p.id}
              onClick={() => go(p.id)}
              className={clsx(
                "inline-flex items-center gap-1 rounded-full border px-3 py-0.5 text-[12.5px]",
                selectedId === p.id ? "border-accent bg-accent-muted text-accent" : "border-line text-muted hover:text-fg",
              )}
            >
              <span>{p.icon ?? "💼"}</span>
              {p.name}
            </button>
          ))}
          <button
            type="button"
            data-testid="new-portfolio"
            onClick={() => setForm("create")}
            className="inline-flex items-center gap-1 rounded-full border border-dashed border-line px-2.5 py-0.5 text-[12.5px] text-muted hover:text-fg"
          >
            <Plus className="size-3.5" aria-hidden /> พอร์ตใหม่
          </button>
        </nav>
        <span className="flex-1" />
        {brief ? (
          <button type="button" onClick={() => setForm("edit")} className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12px] text-muted hover:bg-layer-2">
            <Settings2 className="size-3.5" aria-hidden /> ตั้งค่าพอร์ต
          </button>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {error && !portfolios.length ? <p className="text-[12.5px] text-error">โหลดรายการพอร์ตไม่ได้: {error}</p> : null}
        {!loaded || !selectedId ? (
          <div className="h-48 animate-pulse rounded-md bg-layer-1" />
        ) : selectedId === ALL ? (
          <div className="space-y-3">
            <PortfolioOverview revision={revision} onSelect={go} />
            <WeeklyReports names={names} revision={revision} />
          </div>
        ) : (
          <BookDetail key={selectedId} id={selectedId} brief={brief} />
        )}
      </div>

      {form === "create" ? <PortfolioFormDialog onSubmit={create} onClose={() => setForm(null)} /> : null}
      {form === "edit" && brief ? <PortfolioFormDialog existing={brief} onSubmit={edit} onDelete={remove} onClose={() => setForm(null)} /> : null}
    </div>
  );
}
