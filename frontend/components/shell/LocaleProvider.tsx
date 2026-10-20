"use client";

/** Seeds the language from the server-read cookie before anything renders, + the TH/EN switch. */

import { Languages } from "lucide-react";
import { useState, type ReactNode } from "react";

import { InitialLocale, useLocale, useLocaleStore, useT, type Locale } from "@/lib/i18n";

export function LocaleProvider({ initial, children }: { initial: Locale; children: ReactNode }) {
  useState(() => {
    // once per mount (per request on the server): `tr()` and the hooks read the same language
    if (useLocaleStore.getState().locale !== initial) useLocaleStore.setState({ locale: initial });
    return null;
  });
  return <InitialLocale.Provider value={initial}>{children}</InitialLocale.Provider>;
}

export function LocaleToggle({ compact }: { compact?: boolean }) {
  const locale = useLocale();
  const setLocale = useLocaleStore((s) => s.setLocale);
  const t = useT();
  const next: Locale = locale === "th" ? "en" : "th";
  return (
    <button
      type="button"
      data-testid="locale-toggle"
      onClick={() => setLocale(next)}
      title={t("เปลี่ยนภาษาเป็น English", "Switch language to ไทย")}
      className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12px] text-muted hover:bg-layer-2 hover:text-fg"
    >
      <Languages className="size-4" aria-hidden />
      {compact ? null : <span>{locale === "th" ? "ไทย" : "English"}</span>}
    </button>
  );
}
