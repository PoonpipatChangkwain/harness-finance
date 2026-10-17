/**
 * Layout + inspector UI state (phase-08 §8.1). Column widths, inspector open
 * state/tab and theme persist in localStorage; every storage access is
 * wrapped so a blocked/private storage just falls back to defaults.
 */

"use client";

import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import type { LlmChoice } from "@/lib/api";

export type ThemeMode = "system" | "light" | "dark";

/** Column limits, after deepseek-harness ui-layout `columns.ts` (ui-spec §3). */
export const COLUMNS = {
  sidebar: { default: 240, min: 200, max: 360, collapsed: 56 },
  inspector: { default: 380, min: 320, max: 640 },
} as const;

export const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value);
    } catch {
      // storage full / blocked: layout just is not remembered
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
    } catch {
      // ignore
    }
  },
};

interface UiState {
  sidebarWidth: number;
  inspectorWidth: number;
  inspectorOpen: boolean;
  inspectorTab: string;
  theme: ThemeMode;
  /** what the inspector shows: the run the user last looked at, and a provenance record to focus */
  selectedRunId: string | null;
  focusProvenanceId: string | null;
  /** (P11) graph node picked in the pipeline: the inspector Node/Debate tabs show it */
  selectedNode: string | null;
  selectNode: (node: string | null, tab?: string) => void;
  mobileSidebarOpen: boolean;
  paletteOpen: boolean;
  /** model picked in the composer; null = the backend's configured default chain */
  llmChoice: LlmChoice | null;
  setLlmChoice: (choice: LlmChoice | null) => void;
  /**
   * (P17) "ข้าม — วิเคราะห์เลย" on a DiscoveryCard: the NEXT message is sent with
   * `skip_discovery: true` (shown as a pill in the Composer), then it resets. Never persisted.
   */
  skipDiscovery: boolean;
  setSkipDiscovery: (skip: boolean) => void;
  /** < 1024px the inspector is a drawer: its open state is separate and never persisted */
  drawerMode: boolean;
  drawerOpen: boolean;
  setDrawerMode: (drawer: boolean) => void;
  setSidebarWidth: (w: number) => void;
  setInspectorWidth: (w: number) => void;
  setInspectorOpen: (open: boolean) => void;
  toggleInspector: () => void;
  setInspectorTab: (tab: string) => void;
  setTheme: (t: ThemeMode) => void;
  selectRun: (runId: string | null) => void;
  focusProvenance: (runId: string, provenanceId: string) => void;
  /** (P13) a number that is not part of a run (portfolio analytics): show its lineage */
  focusRecord: (provenanceId: string) => void;
  /** (P13 Settings › Appearance) UI scale: 0.9 · 1 · 1.1 · 1.2 */
  fontScale: number;
  setFontScale: (scale: number) => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setPaletteOpen: (open: boolean) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarWidth: COLUMNS.sidebar.default,
      inspectorWidth: COLUMNS.inspector.default,
      inspectorOpen: true,
      inspectorTab: "trajectory",
      theme: "system",
      selectedRunId: null,
      focusProvenanceId: null,
      selectedNode: null,
      selectNode: (node, tab = "node") =>
        set((s) =>
          node === null
            ? { selectedNode: null }
            : {
                selectedNode: node,
                inspectorTab: tab,
                ...(s.drawerMode ? { drawerOpen: true } : { inspectorOpen: true }),
              },
        ),
      mobileSidebarOpen: false,
      paletteOpen: false,
      llmChoice: null,
      setLlmChoice: (llmChoice) => set({ llmChoice }),
      skipDiscovery: false,
      setSkipDiscovery: (skipDiscovery) => set({ skipDiscovery }),
      drawerMode: false,
      drawerOpen: false,
      setDrawerMode: (drawerMode) => set({ drawerMode, drawerOpen: false }),
      setSidebarWidth: (w) => set({ sidebarWidth: clamp(w, COLUMNS.sidebar.min, COLUMNS.sidebar.max) }),
      setInspectorWidth: (w) => set({ inspectorWidth: clamp(w, COLUMNS.inspector.min, COLUMNS.inspector.max) }),
      setInspectorOpen: (open) => set((s) => (s.drawerMode ? { drawerOpen: open } : { inspectorOpen: open })),
      toggleInspector: () =>
        set((s) => (s.drawerMode ? { drawerOpen: !s.drawerOpen } : { inspectorOpen: !s.inspectorOpen })),
      setInspectorTab: (tab) => set({ inspectorTab: tab }),
      setTheme: (theme) => set({ theme }),
      selectRun: (runId) => set({ selectedRunId: runId }),
      focusProvenance: (runId, provenanceId) =>
        set((s) => ({
          selectedRunId: runId,
          focusProvenanceId: provenanceId,
          inspectorTab: "provenance",
          ...(s.drawerMode ? { drawerOpen: true } : { inspectorOpen: true }),
        })),
      focusRecord: (provenanceId) =>
        set((s) => ({
          focusProvenanceId: provenanceId,
          inspectorTab: "provenance",
          ...(s.drawerMode ? { drawerOpen: true } : { inspectorOpen: true }),
        })),
      fontScale: 1,
      setFontScale: (fontScale) => set({ fontScale: clamp(fontScale, 0.8, 1.3) }),
      setMobileSidebarOpen: (open) => set({ mobileSidebarOpen: open }),
      setPaletteOpen: (open) => set({ paletteOpen: open }),
    }),
    {
      name: "fh-ui",
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({
        sidebarWidth: s.sidebarWidth,
        inspectorWidth: s.inspectorWidth,
        inspectorOpen: s.inspectorOpen,
        inspectorTab: s.inspectorTab,
        theme: s.theme,
        fontScale: s.fontScale,
        llmChoice: s.llmChoice,
      }),
    },
  ),
);
