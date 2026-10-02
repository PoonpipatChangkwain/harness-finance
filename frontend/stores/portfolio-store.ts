"use client";

/**
 * Portfolio page state shared between the main column and the AI Advisor inspector tab:
 * the book list, which book is open ("all" = the cross-portfolio overview), the advisor
 * run each book follows, and signals that make the page reload / scroll to the proposal.
 */

import { create } from "zustand";

import { portfolioApi, type PortfolioBrief } from "@/lib/portfolio-api";

export const ALL = "all";
export const DEFAULT_PORTFOLIO_ID = "pf_default";

interface PortfolioStoreState {
  portfolios: PortfolioBrief[];
  loaded: boolean;
  error: string | null;
  /** open book id, or ALL for the overview */
  selectedId: string | null;
  /** book → the advisor run shown in the proposal section / advisor panel */
  advisorRun: Record<string, string>;
  /** bumped when a book changed (save, import, apply) so every view reloads */
  revision: number;
  /** bumped when an advisor run reaches approval: the page scrolls to the proposal */
  proposalFocus: number;
  reload: () => Promise<void>;
  select: (id: string | null) => void;
  setAdvisorRun: (portfolioId: string, runId: string) => void;
  bump: () => void;
  focusProposal: () => void;
}

export const usePortfolioStore = create<PortfolioStoreState>((set) => ({
  portfolios: [],
  loaded: false,
  error: null,
  selectedId: null,
  advisorRun: {},
  revision: 0,
  proposalFocus: 0,
  reload: async () => {
    try {
      const { portfolios } = await portfolioApi.list();
      set({ portfolios, loaded: true, error: null });
    } catch (err) {
      set({ loaded: true, error: err instanceof Error ? err.message : String(err) });
    }
  },
  select: (selectedId) => set({ selectedId }),
  setAdvisorRun: (portfolioId, runId) => set((s) => ({ advisorRun: { ...s.advisorRun, [portfolioId]: runId } })),
  bump: () => set((s) => ({ revision: s.revision + 1 })),
  focusProposal: () => set((s) => ({ proposalFocus: s.proposalFocus + 1 })),
}));

/** Which view to open when the URL does not say: the only book, the overview, or a new default book. */
export function defaultSelection(portfolios: PortfolioBrief[]): string {
  if (portfolios.length === 0) return DEFAULT_PORTFOLIO_ID;
  if (portfolios.length === 1) return portfolios[0]!.id;
  return ALL;
}
