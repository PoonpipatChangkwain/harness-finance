"use client";

/** Research empty state (ui-spec §9): scenario cards that fill the composer. */

import { Puzzle } from "lucide-react";

import { skillTitle } from "@/components/conversation/Composer";
import type { SkillInfo } from "@/lib/api";

const EXAMPLES: Record<string, string> = {
  "earnings-review": "AAPL",
  "technical-analysis": "PTT",
  "company-valuation": "ประเมินมูลค่า AAPL ด้วย DCF",
  "compare-companies": "เปรียบเทียบ PTT TOP BCP",
  "macro-outlook": "ภาพรวมดอกเบี้ยและเงินเฟ้อตอนนี้",
  "news-digest": "ข่าวสำคัญของ NVDA สัปดาห์นี้",
  "sector-scan": "สแกนกลุ่มพลังงานในตลาดไทย",
  "sentiment-check": "sentiment ของ TSLA ตอนนี้",
  "investment-memo": "เขียน memo การลงทุน MSFT",
};

const ORDER = Object.keys(EXAMPLES);

export function ScenarioCards({ skills, onPick }: { skills: SkillInfo[]; onPick: (skill: string, example: string) => void }) {
  const cards = skills
    .filter((s) => s.available && EXAMPLES[s.name])
    .sort((a, b) => ORDER.indexOf(a.name) - ORDER.indexOf(b.name))
    .slice(0, 6);
  return (
    <div data-testid="scenario-cards" className="space-y-4 pt-[8vh]">
      <div className="text-center">
        <h2 className="text-[18px] font-semibold">เริ่มวิจัย</h2>
        <p className="mt-1 text-[13px] text-muted">เลือก scenario หรือพิมพ์คำถามได้เลย · ทุกตัวเลขในคำตอบมีแหล่งที่มา</p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {cards.map((s) => (
          <button
            key={s.name}
            type="button"
            onClick={() => onPick(s.name, EXAMPLES[s.name] ?? "")}
            className="rounded-lg border border-line bg-layer-1 p-3 text-left hover:border-accent"
          >
            <span className="flex items-center gap-1.5 text-[13.5px] font-medium">
              <Puzzle className="size-3.5 text-accent" aria-hidden /> {skillTitle(s.name)}
            </span>
            <span className="mt-1 line-clamp-2 block text-[12px] text-muted">{s.description}</span>
            <span className="mt-2 block text-[12px] text-subtle">เช่น “{EXAMPLES[s.name]}”</span>
          </button>
        ))}
        {skills.length === 0 ? <p className="col-span-full text-center text-[12.5px] text-subtle">กำลังโหลด scenario…</p> : null}
      </div>
    </div>
  );
}
