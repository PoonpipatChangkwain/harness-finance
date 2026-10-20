/**
 * UI language (Thai / English). Strings stay next to the code that shows them as
 * pairs — `t("ไทย", "English")` — so there is no key table to keep in sync for two
 * languages. Technical terms that were already English (run, provenance, Scratchpad…)
 * are not wrapped and read the same in both.
 *
 * The choice lives in the `fh-locale` cookie: the root layout reads it on the server and
 * hands it to `LocaleProvider`, which seeds the store and the `InitialLocale` context.
 * Components call `useT()` (subscribes → re-renders on switch); its server/hydration
 * snapshot is that context value, so SSR and hydration print the cookie's language
 * (zustand's own hook would use the store's *initial* state there). Plain functions in
 * lib/ that build labels call `tr()` and are only used during a render of a component
 * that already called `useT()`. On the server `tr()` reads the module store the provider
 * seeded for the request — fine for this single-user app, not isolated per request.
 *
 * Server code (the root layout) imports `lib/i18n-config` instead: this module creates a
 * React context.
 */

import { createContext, useContext, useSyncExternalStore } from "react";
import { create } from "zustand";

import { DEFAULT_LOCALE, LOCALE_COOKIE, type Locale } from "./i18n-config";

export { DEFAULT_LOCALE, LOCALE_COOKIE, LOCALES, isLocale, type Locale } from "./i18n-config";

interface LocaleState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

export const useLocaleStore = create<LocaleState>()((set) => ({
  locale: DEFAULT_LOCALE,
  setLocale: (locale) => {
    set({ locale });
    try {
      document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=31536000; samesite=lax`;
      document.documentElement.lang = locale;
    } catch {
      // no document (tests / SSR): the in-memory choice still applies
    }
  },
}));

/** Pick the string for the current language (non-reactive: see module doc). */
export function tr(th: string, en: string): string {
  return useLocaleStore.getState().locale === "en" ? en : th;
}

export type Translate = typeof tr;

const pickTh: Translate = (th) => th;
const pickEn: Translate = (_th, en) => en;

/** The cookie's language for this render; null outside a `LocaleProvider` (tests). */
export const InitialLocale = createContext<Locale | null>(null);

const subscribe = (onChange: () => void) => useLocaleStore.subscribe(onChange);
const current = () => useLocaleStore.getState().locale;

/** The current language, re-rendering on change; SSR/hydration use the provider's cookie value. */
export function useLocale(): Locale {
  const initial = useContext(InitialLocale);
  return useSyncExternalStore(subscribe, current, () => initial ?? current());
}

/**
 * Subscribe to the language and get the picker; call it in every component that shows text.
 * The picker is stable per language. Inside effects/event handlers use `tr()` instead.
 */
export function useT(): Translate {
  return useLocale() === "en" ? pickEn : pickTh;
}

/** BCP-47 tag for Intl date/number formatting in the current language. */
export function intlLocale(locale: Locale = useLocaleStore.getState().locale): string {
  return locale === "en" ? "en-US" : "th-TH";
}

/**
 * A label map whose values follow the language: `localized({ tp: ["ชน target", "Target hit"] })`
 * reads like a plain `Record<K, string>` (`MAP[key]`, `Object.entries`) but each value is a
 * getter resolved at access time.
 */
export function localized<K extends string>(pairs: Record<K, readonly [th: string, en: string]>): Record<K, string> {
  const out = {} as Record<K, string>;
  for (const key of Object.keys(pairs) as K[]) {
    const [th, en] = pairs[key];
    Object.defineProperty(out, key, { get: () => tr(th, en), enumerable: true });
  }
  return out;
}
