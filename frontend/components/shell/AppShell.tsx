"use client";

/**
 * 3-column app shell (ui-spec §3, after dsh ui-layout `AppFrame.tsx`):
 * Sidebar | main | Inspector with drag handles. ≥ 1280 full sidebar,
 * < 1280 icon sidebar, < 1024 inspector becomes a drawer, < 768 the sidebar
 * becomes a sheet. Shortcuts: Ctrl+K palette, Ctrl+. inspector.
 */

import { Menu, PanelRight } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { useBreakpoint } from "@/lib/hooks";
import { useT } from "@/lib/i18n";
import { COLUMNS, useUiStore } from "@/stores/ui-store";

import { CommandPalette } from "./CommandPalette";
import { ConnectionBanner } from "./ConnectionBanner";
import { DragHandle } from "./DragHandle";
import { Inspector } from "./Inspector";
import { InspectorRegistryProvider } from "./InspectorRegistry";
import { Sidebar } from "./Sidebar";
import { ThemeController } from "./ThemeController";

export function AppShell({ children }: { children: ReactNode }) {
  const t = useT();
  const bp = useBreakpoint();
  const ui = useUiStore();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        useUiStore.getState().setPaletteOpen(!useUiStore.getState().paletteOpen);
      } else if (mod && e.key === ".") {
        e.preventDefault();
        useUiStore.getState().toggleInspector();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Layout is chosen by CSS breakpoints (right on first paint, before JS);
  // the breakpoint hook only decides which open-state the inspector buttons drive.
  const drawerInspector = bp === "sm" || bp === "md";
  const setDrawerMode = ui.setDrawerMode;
  useEffect(() => setDrawerMode(drawerInspector), [drawerInspector, setDrawerMode]);
  const inspectorVisible = drawerInspector ? ui.drawerOpen : ui.inspectorOpen;

  return (
    <InspectorRegistryProvider>
      <ThemeController />
      <div className="flex h-dvh flex-col overflow-hidden">
        <ConnectionBanner />
        <div className="flex min-h-0 flex-1">
          {/* ≥ 1280: full sidebar + drag handle · 768–1279: icon sidebar · < 768: sheet */}
          <div style={{ width: ui.sidebarWidth }} className="hidden shrink-0 border-r border-line xl:block">
            <Sidebar compact={false} />
          </div>
          <div className="hidden xl:flex">
            <DragHandle width={ui.sidebarWidth} onWidth={ui.setSidebarWidth} side="left" label="Resize sidebar" />
          </div>
          <div style={{ width: COLUMNS.sidebar.collapsed }} className="hidden shrink-0 border-r border-line md:block xl:hidden">
            <Sidebar compact />
          </div>

          <main className="relative flex min-w-0 flex-1 flex-col">
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line px-2 md:hidden">
              <button
                type="button"
                aria-label="Open menu"
                onClick={() => ui.setMobileSidebarOpen(true)}
                className="rounded-md p-1.5 text-muted hover:bg-layer-2 hover:text-fg"
              >
                <Menu className="size-4" />
              </button>
              <span className="text-[13px] font-semibold">Finance Harness</span>
              <span className="flex-1" />
              <button
                type="button"
                aria-label="Open inspector"
                onClick={() => ui.setInspectorOpen(true)}
                className="rounded-md p-1.5 text-muted hover:bg-layer-2 hover:text-fg"
              >
                <PanelRight className="size-4" />
              </button>
            </div>
            {!inspectorVisible ? (
              <button
                type="button"
                title={t("เปิด inspector (Ctrl+.)", "Open inspector (Ctrl+.)")}
                aria-label="Open inspector"
                onClick={() => ui.setInspectorOpen(true)}
                className="absolute right-2 top-2 z-20 hidden rounded-md border border-line bg-layer-1 p-1.5 text-muted hover:text-fg md:block"
              >
                <PanelRight className="size-4" />
              </button>
            ) : null}
            {children}
          </main>

          {/* ≥ 1024: inspector column · < 1024: drawer */}
          {ui.inspectorOpen ? (
            <>
              <div className="hidden lg:flex">
                <DragHandle width={ui.inspectorWidth} onWidth={ui.setInspectorWidth} side="right" label="Resize inspector" />
              </div>
              <div style={{ width: ui.inspectorWidth }} className="hidden shrink-0 border-l border-line lg:block">
                <Inspector />
              </div>
            </>
          ) : null}
        </div>
      </div>

      {drawerInspector && ui.drawerOpen ? (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/40 lg:hidden" onClick={() => ui.setInspectorOpen(false)}>
          <div className="h-full w-full border-l border-line md:w-[min(420px,90vw)]" onClick={(e) => e.stopPropagation()}>
            <Inspector />
          </div>
        </div>
      ) : null}

      {ui.mobileSidebarOpen ? (
        <div className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={() => ui.setMobileSidebarOpen(false)}>
          <div className="h-full w-[min(280px,85vw)] border-r border-line" onClick={(e) => e.stopPropagation()}>
            <Sidebar compact={false} />
          </div>
        </div>
      ) : null}

      <CommandPalette />
    </InspectorRegistryProvider>
  );
}
