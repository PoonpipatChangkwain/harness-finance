"use client";

/** Research empty state (ui-spec §9): scenario cards that fill the composer. */

import { Puzzle } from "lucide-react";

import { skillTitle } from "@/components/conversation/Composer";
import type { SkillInfo } from "@/lib/api";
import { localized, useT } from "@/lib/i18n";

const EXAMPLES: Record<string, string> = localized({
  "earnings-review": ["AAPL", "AAPL"],
  "technical-analysis": ["PTT", "PTT"],
  "company-valuation": ["ประเมินมูลค่า AAPL ด้วย DCF", "Value AAPL with a DCF"],
  "compare-companies": ["เปรียบเทียบ PTT TOP BCP", "Compare PTT, TOP and BCP"],
  "macro-outlook": ["ภาพรวมดอกเบี้ยและเงินเฟ้อตอนนี้", "Where are rates and inflation now?"],
  "news-digest": ["ข่าวสำคัญของ NVDA สัปดาห์นี้", "Key NVDA news this week"],
  "sector-scan": ["สแกนกลุ่มพลังงานในตลาดไทย", "Scan the energy sector in the Thai market"],
  "sentiment-check": ["sentiment ของ TSLA ตอนนี้", "TSLA sentiment right now"],
  "investment-memo": ["เขียน memo การลงทุน MSFT", "Write an investment memo on MSFT"],
});

const ORDER = Object.keys(EXAMPLES);

export function ScenarioCards({ skills, onPick }: { skills: SkillInfo[]; onPick: (skill: string, example: string) => void }) {
  const t = useT();
  const cards = skills
    .filter((s) => s.available && EXAMPLES[s.name])
    .sort((a, b) => ORDER.indexOf(a.name) - ORDER.indexOf(b.name))
    .slice(0, 6);
  return (
    <div data-testid="scenario-cards" className="space-y-4 pt-[8vh]">
      <div className="text-center">
        <h2 className="text-[18px] font-semibold">{t("เริ่มวิจัย", "Start researching")}</h2>
        <p className="mt-1 text-[13px] text-muted">
          {t("เลือก scenario หรือพิมพ์คำถามได้เลย · ทุกตัวเลขในคำตอบมีแหล่งที่มา", "Pick a scenario or just type a question · every number in the answer is sourced")}
        </p>
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
            <span className="mt-2 block text-[12px] text-subtle">{t("เช่น", "e.g.")} “{EXAMPLES[s.name]}”</span>
          </button>
        ))}
        {skills.length === 0 ? <p className="col-span-full text-center text-[12.5px] text-subtle">{t("กำลังโหลด scenario…", "Loading scenarios…")}</p> : null}
      </div>
    </div>
  );
}
