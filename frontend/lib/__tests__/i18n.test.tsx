import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LocaleProvider, LocaleToggle } from "@/components/shell/LocaleProvider";
import { CellGrid } from "@/components/backtest/CellGrid";
import { EXIT_LABEL, formProblems, type BacktestForm } from "@/lib/backtest";
import { formatRelative } from "@/lib/format";
import { modeLabel } from "@/lib/holdings";
import { intlLocale, isLocale, localized, tr, useLocaleStore, useT } from "@/lib/i18n";
import { MEMORY_CATEGORIES, STATUS_LABEL } from "@/lib/memory-api";

const setLocale = (locale: "th" | "en") => act(() => useLocaleStore.getState().setLocale(locale));

afterEach(() => {
  useLocaleStore.setState({ locale: "th" });
  document.cookie = "fh-locale=; max-age=0; path=/";
});

describe("i18n core", () => {
  it("defaults to Thai and picks per language", () => {
    expect(useLocaleStore.getState().locale).toBe("th");
    expect(tr("ไทย", "English")).toBe("ไทย");
    setLocale("en");
    expect(tr("ไทย", "English")).toBe("English");
    expect(intlLocale()).toBe("en-US");
  });

  it("setLocale writes the cookie the server layout reads, and <html lang>", () => {
    setLocale("en");
    expect(document.cookie).toContain("fh-locale=en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("validates locale values from the cookie", () => {
    expect(isLocale("th")).toBe(true);
    expect(isLocale("en")).toBe(true);
    expect(isLocale("fr")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });

  it("localized() maps read the current language on every access and stay enumerable", () => {
    const map = localized({ a: ["ก", "A"], b: ["ข", "B"] });
    expect(map.a).toBe("ก");
    setLocale("en");
    expect(map.a).toBe("A");
    expect(Object.keys(map)).toEqual(["a", "b"]);
    expect(Object.values(map)).toEqual(["A", "B"]);
  });

  it("useT re-renders a component when the language changes", () => {
    function Probe() {
      const t = useT();
      return <p>{t("สวัสดี", "Hello")}</p>;
    }
    render(<Probe />);
    expect(screen.getByText("สวัสดี")).toBeInTheDocument();
    setLocale("en");
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("LocaleProvider seeds the store from the server-read cookie before children render", () => {
    function Probe() {
      return <p>{useT()("ไทย", "English")}</p>;
    }
    render(
      <LocaleProvider initial="en">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByText("English")).toBeInTheDocument();
  });

  it("LocaleToggle flips the language", () => {
    render(<LocaleToggle />);
    expect(screen.getByText("ไทย")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("locale-toggle"));
    expect(useLocaleStore.getState().locale).toBe("en");
    expect(screen.getByText("English")).toBeInTheDocument();
  });
});

describe("translated labels outside components", () => {
  it("lib label maps and helpers follow the language", () => {
    setLocale("en");
    expect(EXIT_LABEL.tp).toBe("Target hit");
    expect(STATUS_LABEL.candidate).toBe("Pending");
    expect(MEMORY_CATEGORIES[0]).toMatchObject({ id: "investment_style", label: "Investment style" });
    expect(modeLabel("qty_cost")).toBe("Quantity + avg cost");
    expect(modeLabel("qty_pnl_amount", "THB")).toBe("Quantity + P/L (THB)");
    expect(formatRelative(new Date(Date.now() - 5 * 60_000).toISOString())).toBe("5 min ago");
    setLocale("th");
    expect(modeLabel("qty_pnl_amount", "THB")).toBe("จำนวน + กำไร/ขาดทุน (บาท)");
  });

  it("form validation messages follow the language", () => {
    const form = { symbols: [], start: "2026-01-01", end: "2026-02-01", analysts: ["market"], horizon: "position", sim: { capital: "100000" } };
    setLocale("en");
    expect(formProblems(form as unknown as BacktestForm, "2026-10-20")).toContain("Pick at least 1 symbol");
  });

  it("a whole component renders in English", () => {
    setLocale("en");
    render(<CellGrid cells={[]} />);
    expect(screen.getByText("No cells yet — loading prices…")).toBeInTheDocument();
  });
});
