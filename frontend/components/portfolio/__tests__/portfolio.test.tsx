import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { PortfolioToggle } from "@/components/conversation/PortfolioToggle";
import { HoldingsTable } from "@/components/portfolio/HoldingsTable";
import { ImportCsvDialog, parseCsvPreview } from "@/components/portfolio/ImportCsvDialog";
import { sortAssets } from "@/components/portfolio/PortfolioOverview";
import { ProposalTable, buildTargetEdits } from "@/components/portfolio/ProposalTable";
import { ProvValue } from "@/components/portfolio/ProvValue";
import { correlationColor } from "@/components/portfolio/RiskPanel";
import type { EventEnvelope } from "@/lib/events";
import {
  buildPositions,
  declaredTotalPct,
  draftFromPosition,
  emptyRow,
  mergePositions,
  remapErrors,
  validateRows,
  type DraftRow,
} from "@/lib/holdings";
import {
  normalizePreview,
  type EntryError,
  type PortfolioAnalytics,
  type PortfolioBrief,
  type PortfolioData,
  type ProposalCard,
  type SummaryAsset,
} from "@/lib/portfolio-api";
import { fractionToPercentText, percentTextToFraction } from "@/lib/portfolio-format";
import { replay } from "@/stores/run-reducer";
import { FIXTURE_DIR } from "@/tests/fixtures";

const sym = (canonical: string, currency: string, market: string) => ({
  raw: canonical,
  canonical,
  asset_type: "stock",
  market,
  currency,
  display: canonical.replace(".BK", ""),
});

const BOOK: PortfolioData = {
  id: "pf_default",
  name: "พอร์ตหลัก",
  base_currency: "THB",
  cash: "50000",
  positions: [
    { symbol: sym("PTT.BK", "THB", "TH"), quantity: "1000", avg_cost: "30", target_weight: "0.2", declared_weight: null, note: null },
    { symbol: sym("AAPL", "USD", "US"), quantity: "10", avg_cost: "150", target_weight: null, declared_weight: null, note: "core" },
  ],
  updated_at: "2026-09-26T00:00:00Z",
  icon: null,
  color: null,
  provenance_id: "prov_user",
};

const pv = (value: number, id = "prov_x") => ({ value, provenance_id: id });

const ANALYTICS = {
  portfolio_id: "pf_default",
  name: "พอร์ตหลัก",
  base_currency: "THB",
  portfolio_hash: "h",
  computed_at: "2026-09-26T00:00:00Z",
  as_of: "2026-09-25",
  weights_declared_only: false,
  total_value: pv(156500, "prov_total"),
  cash: pv(50000),
  invested_value: pv(106500),
  cost_basis: pv(84000),
  unrealized_pnl: pv(22500),
  unrealized_pnl_pct: pv(0.2679),
  day_pnl: pv(-300),
  day_pnl_pct: pv(-0.0019),
  positions_count: 2,
  holdings: [
    {
      symbol: "PTT.BK", raw: "PTT", display: "PTT", asset_type: "stock", market: "TH", currency: "THB", sector: "Energy",
      quantity: 1000, avg_cost: 30, declared_weight: null, target_weight: 0.2, note: null, status: "ok",
      price: pv(34.5, "prov_price_ptt"), prev_close: 34, fx_rate: pv(1), market_value_local: pv(34500), market_value: pv(34500),
      cost_basis: pv(30000), weight: pv(0.2204, "prov_w"), unrealized_pnl: pv(4500), unrealized_pnl_pct: pv(0.15),
      day_pnl: pv(500), day_change_pct: pv(0.0147), risk_contribution: pv(0.3),
    },
  ],
  weights: { "PTT.BK": 0.2204, AAPL: 0.46, CASH: 0.3195 },
  weights_provenance_id: "prov_w",
  concentration: null,
  exposure: {},
  exposure_provenance: {},
  risk: null,
  fx: {},
  warnings: [],
} as unknown as PortfolioAnalytics;

describe("draft rows ↔ PositionInput", () => {
  it("builds the PUT positions for each entry mode", () => {
    const rows: DraftRow[] = [
      { ...emptyRow("qty_cost"), symbol: "PTT.BK", quantity: "1,000", avgCost: "30.5", targetPct: "20" },
      { ...emptyRow("value_pnl_pct"), symbol: "NVDA", value: "549.96", pnlPct: "14.52" },
      { ...emptyRow("qty_cost"), symbol: "KBANK.BK", quantity: "100", note: "  bank " },
      { ...emptyRow("qty_pnl_pct"), symbol: "SCB.BK", quantity: "200", pnlPct: "-7.69", avgCost: "99" },
      { ...emptyRow("qty_pnl_amount"), symbol: "AAPL", quantity: "10", pnlAmount: "-1,250.5" },
      { ...emptyRow("value_pnl_amount"), symbol: "MSFT", value: "4,000", pnlAmount: "320" },
      { ...emptyRow("cost_total"), symbol: "AOT.BK", quantity: "300", costTotal: "18,000" },
    ];
    expect(buildPositions(rows)).toEqual([
      { symbol: "PTT.BK", mode: "qty_cost", quantity: "1000", avg_cost: "30.5", target_weight: "0.2" },
      { symbol: "NVDA", mode: "value_pnl_pct", value: "549.96", pnl_pct: "0.1452" },
      { symbol: "KBANK.BK", mode: "qty_cost", quantity: "100", note: "bank" },
      // fields of other modes are never sent (avgCost is ignored here)
      { symbol: "SCB.BK", mode: "qty_pnl_pct", quantity: "200", pnl_pct: "-0.0769" },
      { symbol: "AAPL", mode: "qty_pnl_amount", quantity: "10", pnl_amount: "-1250.5" },
      { symbol: "MSFT", mode: "value_pnl_amount", value: "4000", pnl_amount: "320" },
      { symbol: "AOT.BK", mode: "cost_total", quantity: "300", cost_total: "18000" },
    ]);
    expect(buildPositions([{ ...emptyRow("weight"), symbol: "SPY", weightPct: "60%" }])).toEqual([
      { symbol: "SPY", mode: "weight", weight: "0.6" },
    ]);
  });

  it("a parsed PositionInput becomes a draft of the right mode", () => {
    expect(draftFromPosition({ symbol: "NVDA", value: "549.96", pnl_pct: "0.1452" })).toMatchObject({ kind: "value_pnl_pct", value: "549.96", pnlPct: "14.52" });
    expect(draftFromPosition({ symbol: "PTT", quantity: "1000", avg_cost: "32.5", pnl_pct: "0.0769" }).kind).toBe("qty_cost");
    expect(draftFromPosition({ symbol: "A", quantity: "5", pnl_amount: "-20" }).kind).toBe("qty_pnl_amount");
    expect(draftFromPosition({ symbol: "A", quantity: "5", cost_total: "100" }).kind).toBe("cost_total");
    expect(draftFromPosition({ symbol: "A", value: "5", pnl_amount: "1" }).kind).toBe("value_pnl_amount");
    expect(draftFromPosition({ symbol: "A", weight: "0.25" })).toMatchObject({ kind: "weight", weightPct: "25" });
    expect(draftFromPosition({ symbol: "A", mode: "qty_pnl_pct", quantity: "5", pnl_pct: "-0.1" })).toMatchObject({ kind: "qty_pnl_pct", pnlPct: "-10" });
  });

  it("merges new rows into the book and maps a 422 back to them", () => {
    const existing = [
      { symbol: "PTT.BK", mode: "qty_cost" as const, quantity: "1000", target_weight: "0.2", note: "core" },
      { symbol: "AAPL", mode: "qty_cost" as const, quantity: "10" },
    ];
    const added = [
      { symbol: "NVDA", mode: "value_pnl_pct" as const, value: "500" },
      { symbol: "ptt.bk", mode: "qty_pnl_amount" as const, quantity: "1200", pnl_amount: "-50" },
    ];
    const { positions, indexOf } = mergePositions(existing, added);
    expect(indexOf).toEqual([2, 0]);
    expect(positions).toEqual([
      { symbol: "ptt.bk", mode: "qty_pnl_amount", quantity: "1200", pnl_amount: "-50", target_weight: "0.2", note: "core" },
      { symbol: "AAPL", mode: "qty_cost", quantity: "10" },
      { symbol: "NVDA", mode: "value_pnl_pct", value: "500" },
    ]);
    const errs: EntryError[] = [
      { index: 2, symbol: "NVDA", field: "value", message: "ไม่มีราคา" },
      { index: 1, symbol: "AAPL", field: "quantity", message: "x" },
      { index: null, symbol: null, field: null, message: "book" },
    ];
    expect(remapErrors(errs, indexOf)).toEqual([
      { index: 0, symbol: "NVDA", field: "value", message: "ไม่มีราคา" },
      { index: null, symbol: "AAPL", field: "quantity", message: "แถว 2 (AAPL) ในพอร์ต: x" },
      { index: null, symbol: null, field: null, message: "book" },
    ]);
  });

  it("normalizes a preview whose numbers arrive as plain strings", () => {
    const out = normalizePreview({
      rows: [{ index: 0, symbol: "PTT.BK", mode: "qty_pnl_pct", status: "ok", quantity: 1000, avg_cost: "32.5", price: "35", currency: "THB", market_value: { value: "35000", provenance_id: "prov_mv" }, provenance_id: "prov_row" }],
      errors: [],
    });
    const row = out.rows[0]!;
    expect(row.symbol.canonical).toBe("PTT.BK");
    expect(row.quantity).toBe("1000");
    expect(row.price).toEqual({ value: 35, provenance_id: "prov_row" });
    expect(row.market_value).toEqual({ value: 35000, provenance_id: "prov_mv" });
    expect(row.unrealized_pnl).toBeNull();
    expect(row.errors).toEqual([]);
  });

  it("percent text ↔ fraction round-trips without float noise", () => {
    expect(fractionToPercentText("0.07")).toBe("7");
    expect(percentTextToFraction("7")).toBe("0.07");
    expect(percentTextToFraction("12.5%")).toBe("0.125");
    expect(percentTextToFraction("")).toBeUndefined();
  });

  it("validates before sending", () => {
    const errs = validateRows([
      { ...emptyRow("qty_cost"), symbol: "PTT", quantity: "0" },
      { ...emptyRow("qty_cost"), symbol: "ptt", quantity: "10" },
      { ...emptyRow("weight"), symbol: "A", weightPct: "70" },
      { ...emptyRow("weight"), symbol: "B", weightPct: "40" },
    ]);
    expect(errs.map((e) => [e.index, e.field])).toEqual([
      [0, "quantity"],
      [1, "symbol"],
      [null, "weight"],
    ]);
    // the second number of each mode: required where the mode needs it, sign only for P&L
    const modeErrs = validateRows([
      { ...emptyRow("qty_pnl_amount"), symbol: "A", quantity: "10" },
      { ...emptyRow("cost_total"), symbol: "B", quantity: "10", costTotal: "-5" },
      { ...emptyRow("qty_pnl_pct"), symbol: "C", quantity: "10", pnlPct: "-100" },
      { ...emptyRow("value_pnl_amount"), symbol: "D", value: "0", pnlAmount: "-5" },
      { ...emptyRow("qty_pnl_amount"), symbol: "E", quantity: "10", pnlAmount: "-50" },
      { ...emptyRow("value_pnl_pct"), symbol: "F", value: "100" },
    ]);
    expect(modeErrs.map((e) => [e.index, e.field])).toEqual([
      [0, "pnl_amount"],
      [1, "cost_total"],
      [2, "pnl_pct"],
      [3, "value"],
    ]);
    expect(declaredTotalPct([{ ...emptyRow("weight"), weightPct: "70" }, { ...emptyRow("weight"), weightPct: "12.5" }])).toBe(82.5);
  });
});

describe("HoldingsTable", () => {
  it("inline edit → PUT body with every position in order", async () => {
    const onSave = vi.fn(async () => null);
    render(<HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={onSave} onImport={() => {}} />);
    fireEvent.change(screen.getByLabelText("quantity PTT.BK"), { target: { value: "1200" } });
    fireEvent.change(screen.getByLabelText("target AAPL"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("cash"), { target: { value: "40000" } });
    fireEvent.click(screen.getByTestId("save-holdings"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith(
      [
        { symbol: "PTT.BK", mode: "qty_cost", quantity: "1200", avg_cost: "30", target_weight: "0.2" },
        { symbol: "AAPL", mode: "qty_cost", quantity: "10", avg_cost: "150", target_weight: "0.15", note: "core" },
      ],
      "40000",
    );
  });

  it("price / value / weight come from analytics with provenance", () => {
    render(<HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={async () => null} onImport={() => {}} />);
    const row = screen.getAllByTestId("holding-row").find((r) => r.dataset.symbol === "PTT.BK")!;
    const ids = within(row)
      .getAllByTestId("prov-value")
      .map((b) => b.dataset.provenanceId);
    expect(ids).toContain("prov_price_ptt");
    expect(ids).toContain("prov_w");
    expect(row.textContent).toContain("22.04%");
  });

  it("shows a 422 on the row that failed", async () => {
    const errors: EntryError[] = [{ index: 1, symbol: "AAPL", field: "quantity", message: "จำนวนต้องไม่เป็น 0" }];
    const onSave = vi.fn(async () => errors);
    render(<HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={onSave} onImport={() => {}} />);
    fireEvent.change(screen.getByLabelText("note AAPL"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("save-holdings"));
    const err = await screen.findByTestId("row-error");
    expect(err.textContent).toContain("จำนวนต้องไม่เป็น 0");
    const rows = screen.getAllByRole("row");
    const aaplIndex = rows.findIndex((r) => r.getAttribute("data-symbol") === "AAPL");
    expect(rows[aaplIndex + 1]).toBe(err); // right under the AAPL row
  });

  it("%-only mode hides qty/P&L and warns when the total is not 100", () => {
    render(<HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={async () => null} onImport={() => {}} />);
    fireEvent.click(screen.getByTestId("percent-mode"));
    expect(screen.queryByLabelText("quantity PTT.BK")).toBeNull();
    expect(screen.queryByText("P&L")).toBeNull();
    fireEvent.change(screen.getByLabelText("weight PTT.BK"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("weight AAPL"), { target: { value: "50" } });
    const total = screen.getByTestId("declared-total");
    expect(total.textContent).toContain("80.00%");
    expect(total.textContent).toContain("20.00% ถือเป็นเงินสด");
    fireEvent.change(screen.getByLabelText("weight AAPL"), { target: { value: "80" } });
    expect(screen.getByTestId("declared-total").textContent).toContain("เกิน 100%");
  });

  it("+ เพิ่ม opens the add dialog; its rows are merged into the PUT of the book", async () => {
    const onSave = vi.fn(async () => null);
    const search = vi.fn(async () => ({ normalized: sym("NVDA", "USD", "US"), matches: [] }));
    const preview = vi.fn(async () => ({ rows: [], errors: [] }));
    render(
      <HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={onSave} onImport={() => {}} search={search} portfolioId="pf_default" addApi={{ preview }} />,
    );
    fireEvent.click(screen.getByTestId("add-row"));
    const dialog = screen.getByTestId("add-position-dialog");
    expect(screen.getAllByTestId("holding-row")).toHaveLength(2); // no inline draft row
    fireEvent.change(within(dialog).getByTestId("symbol-search"), { target: { value: "NVDA" } });
    fireEvent.mouseDown(await within(dialog).findByTestId("symbol-option", {}, { timeout: 2000 }));
    fireEvent.click(within(dialog).getByTestId("mode-value_pnl_pct"));
    fireEvent.change(within(dialog).getByTestId("add-first"), { target: { value: "549.96" } });
    fireEvent.change(within(dialog).getByTestId("add-second"), { target: { value: "14.52" } });
    fireEvent.click(within(dialog).getByTestId("add-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith(
      [
        { symbol: "PTT.BK", mode: "qty_cost", quantity: "1000", avg_cost: "30", target_weight: "0.2" },
        { symbol: "AAPL", mode: "qty_cost", quantity: "10", avg_cost: "150", note: "core" },
        { symbol: "NVDA", mode: "value_pnl_pct", value: "549.96", pnl_pct: "0.1452" },
      ],
      "50000",
    );
    await waitFor(() => expect(screen.queryByTestId("add-position-dialog")).toBeNull());
  });

  it("a 422 of the merged PUT comes back to the dialog's row", async () => {
    const onSave = vi.fn(async () => [{ index: 2, symbol: "NVDA", field: "value", message: "ดึงราคา NVDA ไม่ได้" }]);
    const search = vi.fn(async () => ({ normalized: sym("NVDA", "USD", "US"), matches: [] }));
    const preview = vi.fn(async () => ({ rows: [], errors: [] }));
    render(<HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={onSave} onImport={() => {}} search={search} addApi={{ preview }} />);
    fireEvent.click(screen.getByTestId("add-row"));
    const dialog = screen.getByTestId("add-position-dialog");
    fireEvent.change(within(dialog).getByTestId("symbol-search"), { target: { value: "NVDA" } });
    fireEvent.mouseDown(await within(dialog).findByTestId("symbol-option", {}, { timeout: 2000 }));
    fireEvent.click(within(dialog).getByTestId("mode-value_pnl_pct"));
    fireEvent.change(within(dialog).getByTestId("add-first"), { target: { value: "549.96" } });
    fireEvent.click(within(dialog).getByTestId("add-save"));
    const err = await within(dialog).findByTestId("item-error");
    expect(err.textContent).toContain("ดึงราคา NVDA ไม่ได้");
  });

  it("a draft row on the table picks its entry mode from a menu of 6", async () => {
    const onSave = vi.fn(async () => null);
    const search = vi.fn(async () => ({ normalized: sym("NVDA", "USD", "US"), matches: [] }));
    render(
      <HoldingsTable book={BOOK} analytics={ANALYTICS} onSave={onSave} onImport={() => {}} search={search} startWith={{ kind: "qty_cost", nonce: 1 }} />,
    );
    const menu = screen.getByTestId("row-mode") as HTMLSelectElement;
    expect(menu.value).toBe("qty_cost");
    expect(Array.from(menu.options).map((o) => o.value)).toEqual([
      "qty_cost",
      "qty_pnl_pct",
      "qty_pnl_amount",
      "value_pnl_pct",
      "value_pnl_amount",
      "cost_total",
    ]);
    const input = screen.getAllByTestId("symbol-search").at(-1)!;
    fireEvent.change(input, { target: { value: "NVDA" } });
    fireEvent.mouseDown(await screen.findByTestId("symbol-option", {}, { timeout: 2000 }));
    expect(screen.getByRole("option", { name: "จำนวน + กำไร/ขาดทุน (USD)" })).toBeTruthy();
    fireEvent.change(screen.getByTestId("row-mode"), { target: { value: "qty_pnl_amount" } });
    expect(screen.queryByLabelText("avg cost NVDA")).toBeNull();
    fireEvent.change(screen.getByLabelText("quantity NVDA"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("pnl amount NVDA"), { target: { value: "-120" } });
    fireEvent.click(screen.getByTestId("save-holdings"));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const positions = (onSave.mock.calls[0] as unknown as [unknown[]])[0];
    expect(positions.at(-1)).toEqual({ symbol: "NVDA", mode: "qty_pnl_amount", quantity: "5", pnl_amount: "-120" });
  });

  it("leaving %-only mode turns a new weight row into a value row with the mode menu", () => {
    render(<HoldingsTable book={null} analytics={null} onSave={async () => null} onImport={() => {}} startWith={{ kind: "weight", nonce: 1 }} />);
    expect(screen.queryByTestId("row-mode")).toBeNull();
    fireEvent.click(screen.getByTestId("percent-mode"));
    expect((screen.getByTestId("row-mode") as HTMLSelectElement).value).toBe("value_pnl_pct");
  });
});

describe("ImportCsvDialog", () => {
  const csv = "symbol,quantity,avg_cost\nPTT,1000,30\nXXXX,5,\n";

  it("parses a preview in the browser", () => {
    const p = parseCsvPreview(`﻿${csv}`);
    expect(p.header).toEqual(["symbol", "quantity", "avg_cost"]);
    expect(p.rows).toHaveLength(2);
  });

  it("server preview errors block Commit; a clean file commits", async () => {
    const preview = vi.fn(async () => ({
      rows: 2,
      positions: [],
      converted: [],
      errors: [{ index: 1, symbol: "XXXX", field: "symbol", message: "symbol ไม่ถูกต้อง" }],
      committed: false,
    }));
    const commit = vi.fn(async () => ({}));
    const onCommitted = vi.fn();
    const { unmount } = render(<ImportCsvDialog portfolioId="pf_default" onClose={() => {}} onCommitted={onCommitted} api={{ preview, commit }} />);
    fireEvent.change(screen.getByTestId("csv-file"), { target: { files: [new File([csv], "pf.csv", { type: "text/csv" })] } });
    const errors = await screen.findByTestId("import-errors");
    expect(errors.textContent).toContain("แถว 2 (XXXX): symbol ไม่ถูกต้อง");
    expect(preview).toHaveBeenCalledWith("pf_default", csv);
    expect(screen.getByTestId("csv-preview").textContent).toContain("PTT");
    expect(screen.getByTestId("commit-import")).toBeDisabled();
    unmount();

    const ok = vi.fn(async () => ({ rows: 1, positions: [BOOK.positions[0]!], converted: [], errors: [], committed: false }));
    render(<ImportCsvDialog portfolioId="pf_default" onClose={() => {}} onCommitted={onCommitted} api={{ preview: ok, commit }} />);
    fireEvent.change(screen.getByTestId("csv-file"), { target: { files: [new File(["symbol,quantity\nPTT,1000\n"], "ok.csv")] } });
    await screen.findByTestId("server-preview");
    fireEvent.click(screen.getByTestId("commit-import"));
    await waitFor(() => expect(onCommitted).toHaveBeenCalled());
    expect(commit).toHaveBeenCalledWith("pf_default", "symbol,quantity\nPTT,1000\n");
  });
});

function loadAdvisorRun(): EventEnvelope[] {
  return readFileSync(path.join(FIXTURE_DIR, "advisor-run-edit.jsonl"), "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as EventEnvelope);
}

describe("advisor run (recorded fixture)", () => {
  const events = loadAdvisorRun();
  const runId = events[0]!.run_id;
  const required = events.find((e) => e.type === "approval.required")!;

  it("the reducer pauses at approval, then finishes after the edit", () => {
    const paused = replay(runId, events.filter((e) => e.seq <= required.seq));
    const pending = Object.values(paused.approvals);
    expect(pending).toHaveLength(1);
    expect(pending[0]!.kind).toBe("rebalance_plan");
    expect(pending[0]!.outcome).toBe("pending");
    expect(paused.path).toContain("policy_gate");

    const done = replay(runId, events);
    expect(Object.values(done.approvals)[0]!.outcome).toBe("edited");
    expect(done.status).toBe("completed");
    // portfolio_advisor v2 (P17): report → learn → END
    expect(done.path.slice(-2)).toEqual(["report", "learn"]);
  });

  it("ProposalTable renders the card and Edit → the approval body", () => {
    const view = replay(runId, events.filter((e) => e.seq <= required.seq));
    const approval = Object.values(view.approvals)[0]!;
    const card = approval.proposal as unknown as ProposalCard;
    const onResolve = vi.fn();
    render(<ProposalTable approval={approval} onResolve={onResolve} />);
    expect(screen.getAllByTestId("proposal-row")).toHaveLength(card.rows.length);
    expect(screen.getAllByTestId("policy-result").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByTestId("edit-plan"));
    fireEvent.change(screen.getByLabelText("target PTT.BK"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("target AAPL"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("target cash"), { target: { value: "50" } });
    fireEvent.click(screen.getByTestId("submit-plan-edit"));
    expect(onResolve).toHaveBeenCalledWith({
      outcome: "edited",
      edits: { target_weights: { "PTT.BK": 0.25, AAPL: 0.25 }, cash_target: 0.5 },
    });
  });

  it("Approve / Reject answer without edits", () => {
    const approval = Object.values(replay(runId, events.filter((e) => e.seq <= required.seq)).approvals)[0]!;
    const onResolve = vi.fn();
    render(<ProposalTable approval={approval} onResolve={onResolve} />);
    fireEvent.click(screen.getByTestId("approve-plan"));
    fireEvent.click(screen.getByTestId("reject-plan"));
    expect(onResolve.mock.calls).toEqual([[{ outcome: "approved" }], [{ outcome: "rejected" }]]);
  });

  it("buildTargetEdits keeps the proposed target of untouched rows", () => {
    const rows = [
      { symbol: "A", current: 0.1, target: 0.3, after: 0.3, delta: 0.2, action: "buy", shares: 1, value: 1, rationale: "" },
      { symbol: "B", current: 0.5, target: 0.2, after: 0.2, delta: -0.3, action: "sell", shares: -1, value: -1, rationale: "" },
    ];
    expect(buildTargetEdits(rows, { A: "35" }, "")).toEqual({ target_weights: { A: 0.35, B: 0.2 } });
  });
});

describe("small pieces", () => {
  it("ProvValue shows the provenance record on hover", async () => {
    const load = vi.fn(async () => ({
      id: "prov_total", kind: "compute" as const, value: 156500, unit: "THB", source: "compute", tool: null,
      calculation: "compute.portfolio.total_value@v1", params: {}, inputs: [], agent: null, run_id: null,
      as_of: "2026-09-25", fetched_at: "", url: null,
    }));
    render(<ProvValue pv={{ value: 156500, provenance_id: "prov_total" }} load={load} />);
    const btn = screen.getByTestId("prov-value");
    expect(btn.textContent).toBe("156,500.00");
    fireEvent.mouseEnter(btn.parentElement!);
    const pop = await screen.findByTestId("prov-popover");
    await waitFor(() => expect(pop.textContent).toContain("compute.portfolio.total_value@v1"));
    expect(load).toHaveBeenCalledWith("prov_total");
  });

  it("PortfolioToggle sends the chosen books; off = none", () => {
    const books: PortfolioBrief[] = [
      { id: "pf_a", name: "A", base_currency: "THB", icon: null, color: null, cash: null, positions: 2, updated_at: "" },
      { id: "pf_b", name: "B", base_currency: "USD", icon: null, color: null, cash: null, positions: 1, updated_at: "" },
    ];
    const onChange = vi.fn();
    const { rerender } = render(<PortfolioToggle portfolios={books} selected={[]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("portfolio-toggle"));
    expect(onChange).toHaveBeenLastCalledWith(["pf_a"]);
    rerender(<PortfolioToggle portfolios={books} selected={["pf_a"]} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("เลือกพอร์ต"));
    fireEvent.click(within(screen.getByTestId("portfolio-picker")).getByLabelText("B"));
    expect(onChange).toHaveBeenLastCalledWith(["pf_a", "pf_b"]);
    fireEvent.click(screen.getByTestId("portfolio-toggle"));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("overview sorts assets by value or by gain", () => {
    const a = (symbol: string, value: number, pct: number | null) => ({ symbol, value, unrealized_pnl_pct: pct }) as SummaryAsset;
    const list = [a("X", 10, 0.5), a("Y", 30, -0.1), a("Z", 20, null)];
    expect(sortAssets(list, "value").map((x) => x.symbol)).toEqual(["Y", "Z", "X"]);
    expect(sortAssets(list, "pnl").map((x) => x.symbol)).toEqual(["X", "Y", "Z"]);
  });

  it("correlation colors diverge around 0", () => {
    expect(correlationColor(1)).toContain("78, 161, 255");
    expect(correlationColor(-0.5)).toContain("240, 106, 106");
  });
});
