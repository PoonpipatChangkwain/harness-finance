"use client";

/**
 * /memory (phase-17 §17.3, ui-spec §Memory): everything the AI remembers about the user
 * — transparent and under the user's control — plus the shared ReasoningBank.
 * Tabs: "ความจำของฉัน" (profile card · candidates · list by category · add form) and
 * "ReasoningBank" (`?tab=reasoning`). Two columns ≥ 1024 px, one column on phones.
 */

import { clsx } from "clsx";
import { Brain } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { memoryApi, type MemoryCounts, type MemoryItemOut, type MemoryProfileOut } from "@/lib/memory-api";

import { AddMemoryForm } from "./AddMemoryForm";
import { MemoryList } from "./MemoryList";
import { MemoryProfileCard } from "./MemoryProfileCard";
import { ReasoningBank } from "./ReasoningBank";

type Tab = "mine" | "reasoning";

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

function MyMemory() {
  const [items, setItems] = useState<MemoryItemOut[] | null>(null);
  const [counts, setCounts] = useState<MemoryCounts | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [profile, setProfile] = useState<MemoryProfileOut | null | undefined>(undefined);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  const load = useCallback(async () => {
    const [list, prof] = await Promise.allSettled([memoryApi.list({ status: "all" }), memoryApi.profile()]);
    if (list.status === "fulfilled") {
      setItems(list.value.items);
      setCounts(list.value.counts);
      setListError(null);
    } else setListError(errText(list.reason));
    if (prof.status === "fulfilled") {
      setProfile(prof.value);
      setProfileError(null);
    } else setProfileError(errText(prof.reason));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const reload = () => void load();
  const inactive = counts ? counts.superseded + counts.retired : 0;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-3 lg:order-1">
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
          {counts ? (
            <span data-testid="memory-counts" className="num">
              ใช้อยู่ {counts.active} · รอยืนยัน {counts.candidate} · ถูกแทนที่ {counts.superseded} · เลิกใช้ {counts.retired}
            </span>
          ) : null}
          <span className="flex-1" />
          <label className="inline-flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              data-testid="show-inactive"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="accent-[var(--fh-accent)]"
            />
            แสดงที่เลิกใช้แล้ว{inactive ? ` (${inactive})` : ""}
          </label>
        </div>
        {listError ? <p className="text-[12.5px] text-error">โหลดความจำไม่ได้: {listError}</p> : null}
        {items === null && !listError ? <div className="h-48 animate-pulse rounded-md bg-layer-1" /> : null}
        {items ? <MemoryList items={items} showInactive={showInactive} onChanged={reload} /> : null}
      </div>
      <aside className="min-w-0 space-y-3 lg:order-2">
        <MemoryProfileCard profile={profile} error={profileError} />
        <AddMemoryForm onAdded={reload} />
      </aside>
    </div>
  );
}

export function MemoryView() {
  const router = useRouter();
  const search = useSearchParams();
  const tab: Tab = search?.get("tab") === "reasoning" ? "reasoning" : "mine";
  const go = (t: Tab) => router.replace(t === "reasoning" ? "/memory?tab=reasoning" : "/memory");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 pr-12">
        <Brain className="size-4 text-accent" aria-hidden />
        <h1 className="mr-2 text-[15px] font-semibold">Memory</h1>
        <div role="tablist" aria-label="Memory" className="flex gap-1">
          {(
            [
              ["mine", "ความจำของฉัน"],
              ["reasoning", "ReasoningBank"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              data-testid={`memory-tab-${id}`}
              aria-selected={tab === id}
              onClick={() => go(id)}
              className={clsx(
                "rounded-full border px-3 py-0.5 text-[12.5px]",
                tab === id ? "border-accent bg-accent-muted text-accent" : "border-line text-muted hover:text-fg",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div className="mx-auto w-full max-w-6xl">
          <p className="mb-3 text-[12px] text-muted">
            สิ่งที่ AI จำเกี่ยวกับคุณ: ยืนยัน แก้ เลิกใช้ หรือลืมได้ทุกข้อ — ความจำที่เปลี่ยนจะไม่ถูกเขียนทับ (ดูประวัติได้) จนกว่าคุณจะสั่ง “ลืม”
          </p>
          {tab === "mine" ? <MyMemory /> : <ReasoningBank />}
        </div>
      </div>
    </div>
  );
}
