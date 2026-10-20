"use client";

/** Right column: tabs registered by the current page (after dsh ui-sidebar-right `SidebarRight.tsx`). */

import { clsx } from "clsx";
import { PanelRightClose } from "lucide-react";

import { Kbd } from "@/components/ui/Kbd";
import { useUiStore } from "@/stores/ui-store";

import { useInspectorTabsList } from "./InspectorRegistry";
import { useT } from "@/lib/i18n";

export function Inspector() {
  const t = useT();
  const tabs = useInspectorTabsList();
  const active = useUiStore((s) => s.inspectorTab);
  const setTab = useUiStore((s) => s.setInspectorTab);
  const setOpen = useUiStore((s) => s.setInspectorOpen);
  const current = tabs.find((t) => t.id === active) ?? tabs[0];

  return (
    <aside aria-label="Inspector" className="flex h-full min-h-0 flex-col bg-layer-1">
      <div className="flex items-center gap-1 border-b border-line px-2">
        <div role="tablist" className="flex min-w-0 flex-1 gap-0.5 overflow-x-auto">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              type="button"
              aria-selected={current?.id === t.id}
              onClick={() => setTab(t.id)}
              className={clsx(
                "shrink-0 border-b-2 px-2 py-2 text-[12.5px]",
                current?.id === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          title={t("ปิด inspector (Ctrl+.)", "Close inspector (Ctrl+.)")}
          className="rounded-md p-1 text-muted hover:bg-layer-2 hover:text-fg"
        >
          <PanelRightClose className="size-4" aria-hidden />
        </button>
      </div>
      <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto">
        {current ? (
          current.render()
        ) : (
          <div className="p-4 text-[12.5px] text-subtle">
            {t("หน้านี้ยังไม่มี inspector · กด", "This page has no inspector · press")} <Kbd>Ctrl</Kbd>+<Kbd>.</Kbd>{" "}
            {t("เพื่อปิด", "to close")}
          </div>
        )}
      </div>
    </aside>
  );
}
