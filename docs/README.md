# Finance Harness — Implementation Plan

เอกสารชุดนี้เขียนให้ **agent ที่ implement (เช่น Claude Sonnet)** ใช้ทำงานทีละ phase ต้องอ่าน `../CLAUDE.md` ก่อนเสมอ

| ไฟล์ | ใช้ทำอะไร |
|------|-----------|
| [contracts.md](contracts.md) | interface กลางของทุก layer (provenance, data, tool, LLM, workflow, events, API, storage) **เป็น source of truth** |
| [data-sources.md](data-sources.md) | แหล่งข้อมูลทั้งหมดจาก reference, tier, env และลำดับ failover |
| [ui-spec.md](ui-spec.md) | หน้าตาและพฤติกรรม UI แบบ deepseek-harness (เห็น harness ทำงานทุกขั้น) |
| [phases/](phases/) | แผนแต่ละ phase |

## ลำดับ phase

```
P00 Scaffold
 └─ P01 Domain models + deterministic compute (F1, F2)
     ├─ P02 Data layer core + Tier-A providers (F4, F3)
     │   └─ P03 Tier-B/C providers
     └─ P04 LLM layer + structured output (F4, T5)
         └─ P05 Harness runtime: events, storage, scratchpad, provenance, executor, guards (D2, D3, D5)
             └─ P06 Context management (D1)
                 └─ P07 Agent loop + Skills + Research workflow/API (D4, F5)
                     └─ P08 Frontend shell + Research Chat UI  ← MVP #1 (ใช้งานได้จริง)
                         └─ P09 Graph engine: LangGraph adapter, checkpoint, approval, policy (T1–T4)
                             ├─ P10 Trading workflow backend
                             │   └─ P11 Trading UI                ← MVP #2
                             └─ P12 Portfolio backend + advisor
                                 └─ P13 Portfolio UI + Settings   ← MVP #3
                                     └─ P14 Hardening: evals, E2E, budget, docs
```

| Phase | ไฟล์ | Pillars | ผลลัพธ์ที่เห็นได้ |
|-------|------|---------|------------------|
| 00 | [phase-00-scaffold.md](phases/phase-00-scaffold.md) | F2 | backend/frontend รันได้ มี lint/test/import contract |
| 01 | [phase-01-domain-compute.md](phases/phase-01-domain-compute.md) | F1, F2 | models + compute ที่ test ครบ |
| 02 | [phase-02-data-core.md](phases/phase-02-data-core.md) | F4, F3 | ดึงราคา PTT.BK/AAPL/EURUSD ได้ พร้อม provenance |
| 03 | [phase-03-data-providers.md](phases/phase-03-data-providers.md) | F4 | provider ครบทุก tier และ failover ทำงาน |
| 04 | [phase-04-llm.md](phases/phase-04-llm.md) | F4, T5 | LLM หลาย provider + structured output + fallback |
| 05 | [phase-05-harness-runtime.md](phases/phase-05-harness-runtime.md) | D2, D3, D5, F3 | run manager + SSE events + executor + guards |
| 06 | [phase-06-context.md](phases/phase-06-context.md) | D1 | microcompact + full compaction |
| 07 | [phase-07-agent-skills-research.md](phases/phase-07-agent-skills-research.md) | D4, F5, D1–D5 | research agent ทำงานผ่าน API |
| 08 | [phase-08-frontend-research.md](phases/phase-08-frontend-research.md) | UI | หน้า Research Chat แบบ dsh |
| 09 | [phase-09-graph-engine.md](phases/phase-09-graph-engine.md) | T1–T4 | workflow engine + checkpoint/resume + approval |
| 10 | [phase-10-trading-backend.md](phases/phase-10-trading-backend.md) | T1–T5, F1 | trading pipeline ครบ |
| 11 | [phase-11-trading-ui.md](phases/phase-11-trading-ui.md) | UI | หน้า Trade + graph live |
| 12 | [phase-12-portfolio-backend.md](phases/phase-12-portfolio-backend.md) | F1, T4, T5 | portfolio analytics + advisor |
| 13 | [phase-13-portfolio-ui-settings.md](phases/phase-13-portfolio-ui-settings.md) | UI | หน้า Portfolio + Settings |
| 14 | [phase-14-hardening.md](phases/phase-14-hardening.md) | ทั้งหมด | evals, E2E, budget, docs |
| 15 | [phase-15-backtest.md](phases/phase-15-backtest.md) | F1, T3 | backtest + portfolio simulator |
| 16 | [phase-16-portfolio-entry-claude-code.md](phases/phase-16-portfolio-entry-claude-code.md) | F1, F4, UI | โหมดกรอกพอร์ตแบบโบรกเกอร์ + preview + วางข้อความ; LLM provider `claude_code` (subscription) |

## วิธีส่งงานให้ implementing agent (template)

```
อ่าน Product/CLAUDE.md, Product/docs/contracts.md และ Product/docs/phases/phase-XX-*.md
ทำ phase XX ให้ครบทุกข้อใน "Tasks" และผ่านทุกข้อใน "Definition of Done"
- อ่านไฟล์ใน "อ่านก่อนเริ่ม" ก่อนเขียนโค้ด
- ห้ามทำงานของ phase อื่น ห้ามเปลี่ยน contract โดยไม่แก้ contracts.md และบอกเหตุผล
- เมื่อเสร็จให้รายงาน: ไฟล์ที่สร้าง/แก้, คำสั่งที่รันพร้อมผล (test/lint/typecheck), สิ่งที่ยังไม่ได้ทำหรือเบี่ยงจากแผน
```

## กฎร่วมทุก phase

1. **ห้ามข้าม Definition of Done:** ทุก phase ต้องจบด้วย `ruff check`, `mypy`, `pytest` (backend) และ `pnpm lint`, `pnpm typecheck`, `pnpm test` (frontend) ที่ผ่านทั้งหมด
2. **Reference ต้องอ่านจริง:** ทุก module ที่ port ต้องมี docstring บรรทัดแรกบอกที่มา เช่น `"""Ported from ../dexter/src/agent/microcompact.ts."""`
3. **ไม่มี network ใน unit test:** ใช้ fixtures, `respx` และ fake LLM
4. **ไม่ขยาย scope:** ถ้าเจอสิ่งที่ควรทำแต่ไม่อยู่ใน phase ให้จดไว้ในหัวข้อ "Follow-ups" ของรายงาน
5. **Windows:** ผู้ใช้ใช้ Windows ดังนั้นคำสั่งใน Makefile/scripts ต้องรันได้ทั้ง PowerShell และ bash (ใช้ `uv run`, `pnpm` และ `just` หรือ npm scripts แทน shell-specific syntax)
6. เมื่อ phase เปลี่ยน command ใดๆ ให้อัปเดตหัวข้อ `## Commands` ใน `Product/CLAUDE.md`
7. **เมื่อ phase เสร็จผ่าน Definition of Done ครบทุกข้อ ให้อัปเดตตาราง `## สถานะ phase` ใน `Product/CLAUDE.md` เป็น `✅ เสร็จ` พร้อมวันที่และสรุปสั้นๆ ทันที ก่อนจบงาน** — ไม่ต้องรอผู้ใช้เตือน เพื่อให้ session ใหม่หลัง `/clear` เห็นสถานะล่าสุดจาก `CLAUDE.md` ตรงๆ ไม่ต้องไล่เปิดทุก phase file
