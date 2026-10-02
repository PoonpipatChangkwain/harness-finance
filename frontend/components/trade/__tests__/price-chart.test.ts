import { describe, expect, it } from "vitest";

import { TIMEFRAMES, chartTime, isLiveAsOf } from "@/components/trade/PriceChart";

describe("PriceChart helpers", () => {
  it("keeps daily bars as dates and shifts intraday bars to the local wall clock", () => {
    expect(chartTime("2026-09-28T00:00:00+00:00", false)).toBe("2026-09-28");
    const ts = "2026-09-28T07:05:00+00:00";
    const d = new Date(ts);
    expect(chartTime(ts, true)).toBe(d.getTime() / 1000 - d.getTimezoneOffset() * 60);
  });

  it("is live without asOf or when asOf is today, not for a past analysis date", () => {
    const now = new Date(2026, 8, 28, 10, 0);
    expect(isLiveAsOf(null, now)).toBe(true);
    expect(isLiveAsOf("2026-09-28", now)).toBe(true);
    expect(isLiveAsOf("2026-09-25", now)).toBe(false);
  });

  it("polls faster on shorter timeframes and gives intraday a fixed window", () => {
    const poll = (id: string) => TIMEFRAMES.find((t) => t.id === id)?.pollMs ?? 0;
    expect(poll("1m")).toBeLessThan(poll("1h"));
    expect(TIMEFRAMES.filter((t) => t.id !== "1d").every((t) => t.range !== null)).toBe(true);
    expect(TIMEFRAMES.find((t) => t.id === "1d")?.range).toBeNull();
  });
});
