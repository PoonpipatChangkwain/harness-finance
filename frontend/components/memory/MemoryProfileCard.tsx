"use client";

/** (P17) The investor profile exactly as it is injected into prompts (`GET /memory/profile`). */

import { Eye } from "lucide-react";

import type { MemoryProfileOut } from "@/lib/memory-api";

import { formatDate } from "./bits";

export function MemoryProfileCard({ profile, error }: { profile: MemoryProfileOut | null | undefined; error?: string | null }) {
  return (
    <section data-testid="memory-profile" className="rounded-lg border border-line bg-layer-1">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <Eye className="size-4 text-accent" aria-hidden />
        <h2 className="text-[13.5px] font-semibold">โปรไฟล์ที่ AI เห็น</h2>
        <span className="flex-1" />
        {profile ? (
          <span className="num text-[11px] text-subtle" title="จำนวน token โดยประมาณ (จำกัด 600)">
            ~{profile.token_estimate} tokens
          </span>
        ) : null}
      </div>
      <div className="space-y-2 p-3 text-[12.5px]">
        <p className="text-[11.5px] text-muted">AI เห็นสิ่งนี้ทุกครั้งที่คุยกับคุณ — แก้ได้โดยแก้รายการความจำด้านล่าง</p>
        {profile === undefined && !error ? <div className="h-16 animate-pulse rounded-md bg-layer-2" /> : null}
        {error ? <p className="text-error">โหลดโปรไฟล์ไม่ได้: {error}</p> : null}
        {profile ? (
          profile.profile_text.trim() ? (
            <pre data-testid="memory-profile-text" className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-layer-2/60 p-2 font-sans text-[12.5px] text-fg">
              {profile.profile_text}
            </pre>
          ) : (
            <p className="text-subtle">ยังไม่มีโปรไฟล์ — AI จะเริ่มจำเมื่อคุณเล่าเรื่องการลงทุนของคุณ</p>
          )
        ) : null}
        {profile ? (
          <p className="text-[11px] text-subtle">
            ใช้ {profile.items_used.length} รายการ{profile.updated_at ? ` · อัปเดต ${formatDate(profile.updated_at)}` : ""}
          </p>
        ) : null}
      </div>
    </section>
  );
}
