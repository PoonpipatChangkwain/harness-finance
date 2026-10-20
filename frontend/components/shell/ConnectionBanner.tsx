"use client";

/**
 * "กำลังเชื่อมต่อใหม่…" bar (ui-spec §9): shown while any run stream is
 * reconnecting, or while the backend health check fails (polled every 5s
 * until it answers again).
 */

import { RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useRunStore } from "@/stores/run-store";

export function ConnectionBanner() {
  const t = useT();
  const streamDown = useRunStore((s) => Object.values(s.conn).some((c) => c === "reconnecting"));
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const check = async () => {
      try {
        await api.health();
        if (alive) setBackendDown(false);
      } catch {
        if (alive) setBackendDown(true);
      }
      if (alive) timer = window.setTimeout(check, 5000);
    };
    void check();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  if (!streamDown && !backendDown) return null;
  return (
    <div
      role="status"
      data-testid="connection-banner"
      className="flex items-center justify-center gap-2 bg-warning/15 px-3 py-1 text-[12.5px] text-warning"
    >
      <RefreshCcw className="size-3.5 animate-spin" aria-hidden />
      {backendDown
        ? t("ติดต่อ backend ไม่ได้ · กำลังเชื่อมต่อใหม่…", "Cannot reach the backend · reconnecting…")
        : t("การเชื่อมต่อหลุด · กำลังเชื่อมต่อใหม่…", "Connection lost · reconnecting…")}
    </div>
  );
}
