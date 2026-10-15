/**
 * Phase-16 §16.3: guided add dialog (6 entry modes + live preview) and paste-from-broker dialog.
 * The API objects are injected (no network); preview debounce is 0 here.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddPositionDialog, loadLastMode, type AddPositionApi } from "@/components/portfolio/AddPositionDialog";
import { PasteHoldingsDialog, type PasteApi } from "@/components/portfolio/PasteHoldingsDialog";
import { ApiError, type SymbolInfo } from "@/lib/api";
import type { GuidedMode } from "@/lib/holdings";
import type { EntryError, ParsedHoldings, PositionInput, PositionsPreview, PreviewRow } from "@/lib/portfolio-api";

const sym = (canonical: string, currency: string, market: string): SymbolInfo => ({
  raw: canonical,
  canonical,
  asset_type: "stock",
  market,
  currency,
  display: canonical.replace(".BK", ""),
});
const AAPL = sym("AAPL", "USD", "US");
const pv = (value: number, provenance_id: string) => ({ value, provenance_id });

function previewRow(over: Partial<PreviewRow> = {}): PreviewRow {
  return {
    index: 0,
    symbol: AAPL,
    mode: "qty_cost",
    status: "ok",
    quantity: "10",
    avg_cost: "150",
    price: pv(193.5, "prov_price"),
    price_as_of: "2026-10-13",
    currency: "USD",
    market_value: pv(1935, "prov_mv"),
    cost_basis: pv(1500, "prov_cb"),
    unrealized_pnl: pv(435, "prov_pnl"),
    unrealized_pnl_pct: pv(0.29, "prov_pnl_pct"),
    provenance_id: "prov_row",
    errors: [],
    ...over,
  };
}

const search = vi.fn(async (q: string) => ({ normalized: q.toUpperCase().startsWith("PTT") ? sym("PTT.BK", "THB", "TH") : AAPL, matches: [] }));

function setup(
  opts: {
    portfolioId?: string | null;
    preview?: AddPositionApi["preview"];
    onSave?: (rows: PositionInput[]) => Promise<EntryError[] | null>;
  } = {},
) {
  const preview = vi.fn(opts.preview ?? (async (): Promise<PositionsPreview> => ({ rows: [previewRow()], errors: [] })));
  const quote = vi.fn(async () => ({
    quote: { symbol: "AAPL", price: "193.50", change: null, change_pct: null, ts: "2026-10-13T20:00:00Z", currency: "USD" },
    provider: "yfinance",
    provenance_id: "prov_quote",
  }));
  const onSave = vi.fn(opts.onSave ?? (async () => null));
  const onClose = vi.fn();
  const view = render(
    <AddPositionDialog
      portfolioId={opts.portfolioId === undefined ? "pf_1" : opts.portfolioId}
      onClose={onClose}
      onSave={onSave}
      api={{ preview, quote }}
      search={search}
      previewDelayMs={0}
    />,
  );
  return { preview, quote, onSave, onClose, ...view };
}

async function pick(q = "AAPL") {
  fireEvent.change(screen.getByTestId("symbol-search"), { target: { value: q } });
  fireEvent.mouseDown(await screen.findByTestId("symbol-option", {}, { timeout: 2000 }));
}

const type = (testId: string, value: string) => fireEvent.change(screen.getByTestId(testId), { target: { value } });

beforeEach(() => {
  localStorage.clear();
  search.mockClear();
});

describe("AddPositionDialog", () => {
  const cases: { mode: GuidedMode; first: string; second: string; loss: boolean; body: PositionInput }[] = [
    { mode: "qty_cost", first: "10", second: "150", loss: false, body: { symbol: "AAPL", mode: "qty_cost", quantity: "10", avg_cost: "150" } },
    { mode: "qty_pnl_pct", first: "10", second: "14.52", loss: true, body: { symbol: "AAPL", mode: "qty_pnl_pct", quantity: "10", pnl_pct: "-0.1452" } },
    { mode: "qty_pnl_amount", first: "10", second: "250.5", loss: true, body: { symbol: "AAPL", mode: "qty_pnl_amount", quantity: "10", pnl_amount: "-250.5" } },
    { mode: "value_pnl_pct", first: "549.96", second: "14.52", loss: false, body: { symbol: "AAPL", mode: "value_pnl_pct", value: "549.96", pnl_pct: "0.1452" } },
    { mode: "value_pnl_amount", first: "1,000", second: "120", loss: true, body: { symbol: "AAPL", mode: "value_pnl_amount", value: "1000", pnl_amount: "-120" } },
    { mode: "cost_total", first: "10", second: "1,500", loss: false, body: { symbol: "AAPL", mode: "cost_total", quantity: "10", cost_total: "1500" } },
  ];

  it.each(cases)("mode $mode sends the right positions body", async ({ mode, first, second, loss, body }) => {
    const { preview, onSave } = setup();
    await pick();
    fireEvent.click(screen.getByTestId(`mode-${mode}`));
    type("add-first", first);
    type("add-second", second);
    if (loss) fireEvent.click(screen.getByTestId("sign-loss"));
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("pf_1", [body]));
    fireEvent.click(screen.getByTestId("add-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith([body]));
  });

  it("only the P&L modes show the กำไร / ขาดทุน toggle; money labels follow the symbol currency", async () => {
    setup();
    await pick();
    fireEvent.click(screen.getByTestId("mode-qty_cost"));
    expect(screen.queryByTestId("sign-loss")).toBeNull();
    expect(screen.getByTestId("mode-qty_pnl_amount").textContent).toBe("จำนวน + กำไร/ขาดทุน (USD)");
    expect(screen.getByLabelText("ต้นทุนเฉลี่ย (USD)")).toBeTruthy();
    fireEvent.click(screen.getByTestId("mode-value_pnl_amount"));
    expect(screen.getByTestId("sign-gain").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByLabelText("มูลค่า (USD)")).toBeTruthy();
  });

  it("a typed minus flips the toggle to ขาดทุน instead of staying in the text", async () => {
    const { preview } = setup();
    await pick();
    fireEvent.click(screen.getByTestId("mode-qty_pnl_pct"));
    type("add-first", "10");
    type("add-second", "-5");
    expect((screen.getByTestId("add-second") as HTMLInputElement).value).toBe("5");
    expect(screen.getByTestId("sign-loss").getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("pf_1", [{ symbol: "AAPL", mode: "qty_pnl_pct", quantity: "10", pnl_pct: "-0.05" }]));
  });

  it("remembers the last mode", async () => {
    const { unmount } = setup();
    await pick();
    fireEvent.click(screen.getByTestId("mode-value_pnl_amount"));
    expect(loadLastMode()).toBe("value_pnl_amount");
    unmount();
    setup();
    await pick();
    expect(screen.getByTestId("mode-value_pnl_amount").getAttribute("aria-checked")).toBe("true");
  });

  it("renders the preview numbers of the backend with provenance + the last price of step 1", async () => {
    const { quote } = setup();
    await pick();
    await waitFor(() => expect(quote).toHaveBeenCalledWith("AAPL"));
    expect((await screen.findByTestId("add-quote")).textContent).toContain("193.50");
    fireEvent.click(screen.getByTestId("mode-qty_cost"));
    type("add-first", "10");
    type("add-second", "150");
    const card = screen.getByTestId("add-preview");
    await within(card).findByTestId("pv-value");
    expect(within(card).getByTestId("pv-value").textContent).toContain("1,935.00");
    expect(within(card).getByTestId("pv-price").textContent).toContain("193.50");
    expect(within(card).getByTestId("pv-price").textContent).toContain("2026-10-13");
    expect(within(card).getByTestId("pv-pnl").textContent).toContain("+435.00");
    expect(within(card).getByTestId("pv-pnl").textContent).toContain("+29.00%");
    const ids = within(card)
      .getAllByTestId("prov-value")
      .map((b) => b.dataset.provenanceId);
    expect(ids).toEqual(expect.arrayContaining(["prov_row", "prov_price", "prov_mv", "prov_cb", "prov_pnl", "prov_pnl_pct"]));
    expect(card.textContent).toContain("ระบบคำนวณจากราคาล่าสุด ค่าจะขยับตามตลาด");
  });

  it("a backend row error is shown at its input and blocks saving", async () => {
    const { onSave } = setup({
      preview: async () => ({
        rows: [
          previewRow({
            status: "error",
            mode: "qty_pnl_amount",
            market_value: null,
            errors: [{ index: 0, symbol: "AAPL", field: "pnl_amount", message: "ขาดทุนเกินทุน" }],
          }),
        ],
        errors: [],
      }),
    });
    await pick();
    fireEvent.click(screen.getByTestId("mode-qty_pnl_amount"));
    type("add-first", "10");
    type("add-second", "999999");
    fireEvent.click(screen.getByTestId("sign-loss"));
    expect((await screen.findByTestId("error-second")).textContent).toContain("ขาดทุนเกินทุน");
    expect(screen.queryByTestId("error-first")).toBeNull();
    fireEvent.click(screen.getByTestId("add-save"));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("a 422 from the preview maps to the input by field", async () => {
    setup({
      preview: async () => {
        throw new ApiError(422, "http_error", "invalid", { errors: [{ index: 0, symbol: "AAPL", field: "quantity", message: "จำนวนต้องไม่เป็น 0" }] });
      },
    });
    await pick();
    fireEvent.click(screen.getByTestId("mode-cost_total"));
    type("add-first", "0.0000001");
    type("add-second", "100");
    expect((await screen.findByTestId("error-first")).textContent).toContain("จำนวนต้องไม่เป็น 0");
  });

  it("เพิ่มอีกตัว collects rows; a 422 of the save comes back to the right item", async () => {
    const { onSave } = setup({
      onSave: async () => [{ index: 1, symbol: "PTT.BK", field: "quantity", message: "จำนวนต้องไม่เป็น 0" }],
    });
    await pick("AAPL");
    fireEvent.click(screen.getByTestId("mode-qty_cost"));
    type("add-first", "10");
    fireEvent.click(screen.getByTestId("add-another"));
    expect(screen.getAllByTestId("add-item")).toHaveLength(1);
    await pick("PTT");
    type("add-first", "1000");
    type("add-second", "32.5");
    fireEvent.click(screen.getByTestId("add-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith([
      { symbol: "AAPL", mode: "qty_cost", quantity: "10" },
      { symbol: "PTT.BK", mode: "qty_cost", quantity: "1000", avg_cost: "32.5" },
    ]);
    const items = await screen.findAllByTestId("add-item");
    expect(items).toHaveLength(2);
    expect(within(items[1]!).getByTestId("item-error").textContent).toContain("จำนวนต้องไม่เป็น 0");
    expect(within(items[0]!).queryByTestId("item-error")).toBeNull();
    fireEvent.click(within(items[1]!).getByLabelText("ลบ PTT.BK"));
    expect(screen.getAllByTestId("add-item")).toHaveLength(1);
  });

  it("checks locally before saving (required second number)", async () => {
    const { onSave } = setup();
    await pick();
    fireEvent.click(screen.getByTestId("mode-qty_pnl_amount"));
    type("add-first", "10");
    fireEvent.click(screen.getByTestId("add-save"));
    expect((await screen.findByTestId("local-error-second")).textContent).toContain("กรอกกำไร/ขาดทุน");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("without a book id it previews against the default book", async () => {
    const { preview } = setup({ portfolioId: null });
    await pick();
    fireEvent.click(screen.getByTestId("mode-qty_cost"));
    type("add-first", "1");
    await waitFor(() => expect(preview).toHaveBeenCalledWith(null, [{ symbol: "AAPL", mode: "qty_cost", quantity: "1" }]));
  });
});

describe("PasteHoldingsDialog", () => {
  const PARSED: ParsedHoldings = {
    rows: [
      { symbol: "NVDA", value: "549.96", pnl_pct: "0.1452" },
      { symbol: "PTT", mode: "qty_cost", quantity: "1000", avg_cost: "32.50" },
    ],
    unparsed: ["มูลค่ารวม 1,234,567.00"],
    mode: "native",
    provider: "groq",
    model: "llama-quick",
  };
  const PREVIEW: PositionsPreview = {
    rows: [
      previewRow({ index: 0, symbol: sym("NVDA", "USD", "US"), mode: "value_pnl_pct" }),
      previewRow({ index: 1, symbol: sym("PTT.BK", "THB", "TH"), currency: "THB", provenance_id: "prov_ptt" }),
    ],
    errors: [],
  };

  function setupPaste(parse: PasteApi["parse"], opts: { hasPositions?: boolean; onSave?: (rows: PositionInput[], replace: boolean) => Promise<EntryError[] | null> } = {}) {
    const api = { parse: vi.fn(parse), preview: vi.fn(async () => PREVIEW) };
    const onSave = vi.fn(opts.onSave ?? (async () => null));
    render(<PasteHoldingsDialog portfolioId="pf_1" onClose={() => {}} onSave={onSave} hasPositions={opts.hasPositions} api={api} previewDelayMs={0} />);
    return { api, onSave };
  }

  const TEXT = "NVDA 549.96 USD +14.52%\nPTT 1,000 32.50 35.00 +7.69%\nมูลค่ารวม 1,234,567.00";

  it("parse → preview → save appends with the resolved symbols", async () => {
    const { api, onSave } = setupPaste(async () => PARSED, { hasPositions: true });
    fireEvent.change(screen.getByTestId("paste-text"), { target: { value: TEXT } });
    fireEvent.click(screen.getByTestId("hint-TH"));
    fireEvent.click(screen.getByTestId("paste-parse"));
    await waitFor(() => expect(api.parse).toHaveBeenCalledWith({ text: TEXT, market_hint: "TH" }));
    expect(await screen.findAllByTestId("paste-row")).toHaveLength(2);
    expect(screen.getByTestId("paste-unparsed").textContent).toContain("มูลค่ารวม 1,234,567.00");
    await waitFor(() =>
      expect(api.preview).toHaveBeenCalledWith("pf_1", [
        { symbol: "NVDA", mode: "value_pnl_pct", value: "549.96", pnl_pct: "0.1452" },
        { symbol: "PTT", mode: "qty_cost", quantity: "1000", avg_cost: "32.50" },
      ]),
    );
    await waitFor(() => expect(screen.getAllByTestId("prov-value").length).toBeGreaterThan(0));
    expect(screen.getByTestId("save-append").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByTestId("paste-save"));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        [
          { symbol: "NVDA", mode: "value_pnl_pct", value: "549.96", pnl_pct: "0.1452" },
          { symbol: "PTT.BK", mode: "qty_cost", quantity: "1000", avg_cost: "32.50" },
        ],
        false,
      ),
    );
  });

  it("edits a parsed row, then replaces the whole book", async () => {
    const { api, onSave } = setupPaste(async () => PARSED, { hasPositions: true });
    fireEvent.change(screen.getByTestId("paste-text"), { target: { value: TEXT } });
    fireEvent.click(screen.getByTestId("paste-parse"));
    await waitFor(() => expect(api.parse).toHaveBeenCalledWith({ text: TEXT, market_hint: null }));
    await screen.findAllByTestId("paste-row");
    await waitFor(() => expect(screen.getAllByTestId("prov-value").length).toBeGreaterThan(0)); // currencies known
    fireEvent.change(screen.getByLabelText("โหมดกรอก NVDA"), { target: { value: "value_pnl_amount" } });
    fireEvent.change(screen.getByLabelText("กำไร/ขาดทุน (USD) NVDA"), { target: { value: "-30" } });
    await waitFor(() =>
      expect(api.preview).toHaveBeenLastCalledWith("pf_1", [
        { symbol: "NVDA", mode: "value_pnl_amount", value: "549.96", pnl_amount: "-30" },
        { symbol: "PTT", mode: "qty_cost", quantity: "1000", avg_cost: "32.50" },
      ]),
    );
    await waitFor(() => expect(screen.getAllByTestId("prov-value").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByTestId("save-replace"));
    fireEvent.click(screen.getByTestId("paste-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const [rows, replace] = onSave.mock.calls[0]!;
    expect(replace).toBe(true);
    expect(rows[0]).toEqual({ symbol: "NVDA", mode: "value_pnl_amount", value: "549.96", pnl_amount: "-30" });
  });

  it("a 422 of the save is shown on the row", async () => {
    setupPaste(async () => PARSED, { onSave: async () => [{ index: 0, symbol: "NVDA", field: "value", message: "ราคา NVDA ไม่มี" }] });
    fireEvent.change(screen.getByTestId("paste-text"), { target: { value: TEXT } });
    fireEvent.click(screen.getByTestId("paste-parse"));
    await screen.findAllByTestId("paste-row");
    await waitFor(() => expect(screen.getAllByTestId("prov-value").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByTestId("paste-save"));
    const row = (await screen.findAllByTestId("paste-row"))[0]!;
    await waitFor(() => expect(within(row).getByTestId("paste-row-error").textContent).toContain("ราคา NVDA ไม่มี"));
  });

  it("503 llm_not_configured → tells the user to set up an LLM in Settings", async () => {
    setupPaste(async () => {
      throw new ApiError(503, "llm_not_configured", "no LLM", { error: { type: "llm_not_configured", message: "no LLM" } });
    });
    fireEvent.change(screen.getByTestId("paste-text"), { target: { value: TEXT } });
    fireEvent.click(screen.getByTestId("paste-parse"));
    const msg = await screen.findByTestId("paste-llm-missing");
    expect(msg.textContent).toContain("ตั้งค่า LLM ใน Settings ก่อน");
    expect(within(msg).getByRole("link").getAttribute("href")).toBe("/settings");
  });

  it("fallback_parsed is a subtle notice; failed is an error", async () => {
    setupPaste(async () => ({ ...PARSED, mode: "fallback_parsed" }));
    fireEvent.change(screen.getByTestId("paste-text"), { target: { value: TEXT } });
    fireEvent.click(screen.getByTestId("paste-parse"));
    expect((await screen.findByTestId("paste-fallback")).textContent).toContain("ตรวจตัวเลขอีกครั้ง");
    expect(screen.queryByTestId("paste-failed")).toBeNull();
  });

  it("failed parse shows an error and no rows", async () => {
    setupPaste(async () => ({ rows: [], unparsed: [TEXT], mode: "failed", provider: "groq", model: "m" }));
    fireEvent.change(screen.getByTestId("paste-text"), { target: { value: TEXT } });
    fireEvent.click(screen.getByTestId("paste-parse"));
    expect((await screen.findByTestId("paste-failed")).textContent).toContain("AI อ่านข้อความนี้ไม่ได้");
    expect(screen.queryByTestId("paste-rows")).toBeNull();
    expect(screen.getByTestId("paste-save")).toBeDisabled();
  });
});
