import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/AppShell";

export default function ShellLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
