"use client";

/**
 * Inspector tab registry (concept of dsh ui-sidebar-right `tab-registry.ts`):
 * each page registers its own tabs; the shell's Inspector renders them.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export interface InspectorTab {
  id: string;
  label: string;
  render: () => ReactNode;
}

interface Registry {
  tabs: InspectorTab[];
  setTabs: (tabs: InspectorTab[]) => void;
}

const RegistryContext = createContext<Registry>({ tabs: [], setTabs: () => {} });

export function InspectorRegistryProvider({ children }: { children: ReactNode }) {
  const [tabs, setTabs] = useState<InspectorTab[]>([]);
  return <RegistryContext.Provider value={{ tabs, setTabs }}>{children}</RegistryContext.Provider>;
}

export function useInspectorTabsList(): InspectorTab[] {
  return useContext(RegistryContext).tabs;
}

/** Register a page's tabs for as long as the page is mounted. Pass a memoized array. */
export function useInspectorTabs(tabs: InspectorTab[]) {
  const { setTabs } = useContext(RegistryContext);
  useEffect(() => {
    setTabs(tabs);
    return () => setTabs([]);
  }, [tabs, setTabs]);
}
