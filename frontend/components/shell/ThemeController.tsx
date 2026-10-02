"use client";

/** Applies the ui-store theme (system / light / dark) to <html data-theme>. */

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect } from "react";

import { useUiStore, type ThemeMode } from "@/stores/ui-store";

export function ThemeController() {
  const theme = useUiStore((s) => s.theme);
  const fontScale = useUiStore((s) => s.fontScale);
  useEffect(() => {
    // Settings › Appearance: the UI is sized in px, so scale it as a whole
    document.body.style.zoom = fontScale === 1 ? "" : String(fontScale);
  }, [fontScale]);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    const apply = () => {
      const resolved = theme === "system" ? (media?.matches ? "light" : "dark") : theme;
      document.documentElement.dataset.theme = resolved;
    };
    apply();
    if (theme !== "system" || !media) return;
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  return null;
}

const NEXT: Record<ThemeMode, ThemeMode> = { system: "light", light: "dark", dark: "system" };
const ICON = { system: Monitor, light: Sun, dark: Moon } as const;

export function ThemeToggle({ compact }: { compact?: boolean }) {
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const Icon = ICON[theme];
  return (
    <button
      type="button"
      onClick={() => setTheme(NEXT[theme])}
      title={`Theme: ${theme}`}
      className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12px] text-muted hover:bg-layer-2 hover:text-fg"
    >
      <Icon className="size-4" aria-hidden />
      {compact ? null : <span className="capitalize">{theme}</span>}
    </button>
  );
}
