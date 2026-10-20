/** UI language constants, free of React/zustand so the server root layout can import them. */

export type Locale = "th" | "en";

export const LOCALES: readonly Locale[] = ["th", "en"];
export const DEFAULT_LOCALE: Locale = "th";
export const LOCALE_COOKIE = "fh-locale";

export const isLocale = (v: unknown): v is Locale => v === "th" || v === "en";
