"use client";

/** Research session list for the sidebar and command palette. */

import { create } from "zustand";

import { api, ApiError, type SessionListItem } from "@/lib/api";
import { tr } from "@/lib/i18n";

interface SessionState {
  sessions: SessionListItem[];
  loaded: boolean;
  error: string | null;
  reload: () => Promise<void>;
  /** DELETE /sessions/{id}; resolves to an error message, or null when deleted */
  remove: (id: string) => Promise<string | null>;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  loaded: false,
  error: null,
  reload: async () => {
    try {
      const { sessions } = await api.listSessions("research");
      set({ sessions, loaded: true, error: null });
    } catch (err) {
      set({ loaded: true, error: err instanceof Error ? err.message : String(err) });
    }
  },
  remove: async (id) => {
    try {
      await api.deleteSession(id);
      set((s) => ({ sessions: s.sessions.filter((x) => x.id !== id) }));
      return null;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) return tr("session นี้มี run กำลังทำงานอยู่ กด Stop ก่อนแล้วค่อยลบ", "This session has a run in progress — press Stop before deleting");
      return err instanceof Error ? err.message : String(err);
    }
  },
}));
