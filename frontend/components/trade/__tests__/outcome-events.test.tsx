import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EventRiskList } from "@/components/trade/EventRiskList";
import { TradeOutcomePanel } from "@/components/trade/TradeOutcomePanel";
import type { TradeResult } from "@/lib/trade-api";

const result: TradeResult = {
  run_id: "run_1",
  symbol: "GBPJPY=X",
  date: "2026-09-28",
  recorded_at: "2026-09-28T03:20:00Z",
  card: { side: "long", entry: 208.9, stop: 208.4, targets: [209.6, 210.2] },
  outcome: null,
  reflection: null,
  reflection_error: null,
};

describe("TradeOutcomePanel", () => {
  it("ticks TP2 / SL from the decision's own levels", async () => {
    const onSettle = vi.fn(async () => null);
    render(<TradeOutcomePanel result={result} onSettle={onSettle} />);
    fireEvent.click(screen.getByText(/TP2 @ 210.2/));
    await waitFor(() => expect(onSettle).toHaveBeenCalledWith({ outcome: "tp", target_index: 1, note: "" }));
    fireEvent.click(screen.getByText(/SL @ 208.4/));
    await waitFor(() => expect(onSettle).toHaveBeenLastCalledWith({ outcome: "sl", note: "" }));
  });

  it("asks for the exit price of a manual exit", async () => {
    const onSettle = vi.fn(async () => null);
    render(<TradeOutcomePanel result={result} onSettle={onSettle} />);
    fireEvent.click(screen.getByText("ออกเอง…"));
    fireEvent.change(screen.getByLabelText("exit price"), { target: { value: "209.1" } });
    fireEvent.change(screen.getByLabelText("note"), { target: { value: "ออกก่อน BoE" } });
    fireEvent.click(screen.getByText("บันทึก"));
    await waitFor(() =>
      expect(onSettle).toHaveBeenCalledWith({ outcome: "manual_exit", exit_price: 209.1, note: "ออกก่อน BoE" }),
    );
  });

  it("shows R and the lesson once settled", () => {
    const settled: TradeResult = {
      ...result,
      outcome: { kind: "sl", label: "stop-loss hit", exit_price: 208.4, r_multiple: -1, note: "", settled_at: "x" },
      reflection: "Stop sat inside the London-open range; use 1.5 ATR next time.",
    };
    render(<TradeOutcomePanel result={settled} onSettle={vi.fn()} />);
    expect(screen.getByTestId("r-multiple").textContent).toBe("-1.00R");
    expect(screen.getByTestId("reflection").textContent).toContain("London-open range");
  });
});

describe("EventRiskList", () => {
  it("lists high/medium releases and says when the calendar is unavailable", () => {
    const { rerender } = render(
      <EventRiskList
        risk={{
          available: true,
          source: "ForexFactory",
          currencies: ["GBP", "JPY"],
          window_hours: 24,
          events: [
            { at: "2026-09-28T06:00:00+00:00", hours_from_now: 2.5, currency: "GBP", impact: "high",
              title: "GDP q/q", forecast: "0.1%", previous: "0.2%", actual: null },
            { at: "2026-09-28T07:00:00+00:00", hours_from_now: 3.5, currency: "JPY", impact: "low",
              title: "Minor", forecast: null, previous: null, actual: null },
          ],
        }}
      />,
    );
    const rows = screen.getAllByTestId("event-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]?.dataset.impact).toBe("high");
    expect(rows[0]?.textContent).toContain("GDP q/q");
    rerender(<EventRiskList risk={{ available: false, error: "rate limited" }} />);
    expect(screen.getByTestId("event-risk").textContent).toContain("ไม่ได้แปลว่าไม่มีข่าว");
  });
});
