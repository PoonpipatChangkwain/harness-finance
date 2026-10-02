# Phase 14 — Hardening: evals, E2E, budget, observability, docs

## เป้าหมาย
ทำให้ระบบเชื่อถือได้และวัดผลได้:
- eval คุณภาพคำตอบ research (ตามแบบ Dexter)
- E2E test ทั้ง 3 หน้า
- budget/cost ที่มองเห็นได้
- จัดการ error ทุกเส้นทาง
- อัปเดตเอกสาร

## ต้องเสร็จก่อน
P13

## อ่านก่อนเริ่ม
- `../dexter/src/evals/` (run.ts, dataset.ts, evaluator.ts, sampling.ts, options.ts, `dataset/finance_agent.csv`)
- `../TradingAgents/tradingagents/backtest.py`, `graph/settlement.py`
- `../deepseek-harness/docs/testing.md`, `benchmarks/` (แนวคิด gate)
- `../deepseek-harness/SAFETY.md`

## Tasks

### 14.1 Evals (`backend/evals/`)
- dataset `evals/datasets/research_th_us.csv` (question, expected_facts, must_cite: bool, skill?, category) อย่างน้อย 40 ข้อ (ไทย 20 / US 15 / forex-macro 5) และ port รูปแบบคอลัมน์จาก `finance_agent.csv`
- runner `uv run python -m evals.run [--sample N] [--skill X]` รัน research workflow จริง (ต้องมี key) แล้วเก็บผลที่ `evals/results/<timestamp>.jsonl`
- evaluators:
  1. **deterministic:** มี citation ทุกตัวเลข, `unverified_numbers` ว่าง, ไม่ชน guard limit, cost และ latency
  2. **LLM-as-judge:** correctness เทียบ expected_facts (0–1) ตามแบบ `evaluator.ts` ด้วย tier quick และ prompt ที่ fix version
- สรุปเป็นตาราง markdown ต่อ category + ค่าเฉลี่ยของ cost/latency และเทียบกับผลรอบก่อน (regression)
- eval ของ trading: `evals/trading_backtest.py` รัน workflow กับวันที่ในอดีต 10 วัน × 3 symbol (as_of guard ต้องทำงาน) แล้วใช้ settlement คำนวณ hit rate และ alpha vs benchmark (**รายงานเท่านั้น ไม่ใช่เกณฑ์ผ่าน**)

### 14.2 Look-ahead audit
- test ว่าเมื่อ `as_of` อยู่ในอดีต provider ทุกตัวไม่คืนข้อมูลหลัง as_of (ใช้ fixture ที่มีข้อมูลอนาคตปนอยู่) โดยครอบคลุม NEWS, SOCIAL, STATEMENTS และ PRICES

### 14.3 E2E (`frontend/e2e/`, Playwright)
- รัน backend ด้วย `APP_ENV=e2e` ที่ใช้ FakeLLM scripted + fixture providers (ไม่มี network)
- scenarios:
  1. research: สร้าง session → เลือก scenario → เห็น tool rows → citation popover → inspector 4 tabs → stop
  2. trade: PTT → run → graph → approval edit → report; crash + resume (endpoint dev ที่จำลอง failure)
  3. portfolio: กรอก → analytics → advise → approve → apply
  4. reconnect SSE (ปิด/เปิด backend)
- ถ่าย screenshot ทั้ง dark/light ที่ 1440 และ 390 px

### 14.4 Budget & cost
- แสดง cost สะสมต่อวันและต่อเดือนใน sidebar footer (backend `GET /api/usage/summary`)
- `DAILY_BUDGET_USD` ใน settings: ถ้าเกินให้ปฏิเสธ run ใหม่ด้วยข้อความชัดเจน (guard ระดับ app)
- trajectory export: `GET /api/runs/{id}/export` → zip (events.jsonl, scratchpad.jsonl, provenance.json, run.json) สำหรับ debug/audit

### 14.5 Error handling sweep
- ทุก endpoint มี error shape เดียวกัน และทุกหน้ามี error boundary + retry
- provider ทั้งหมด down → research ยังตอบได้ (graceful) พร้อมบอกว่าขาดข้อมูลอะไร
- LLM ทั้งหมด down → run fail พร้อมข้อความแนะนำให้ตรวจ key ใน Settings
- log แบบ structured (JSON) มี run_id และ **redact** secrets (ใช้ test จาก P03 ซ้ำ)

### 14.6 Docs
- อัปเดต `Product/CLAUDE.md` หัวข้อ Commands ให้ครบ
- `Product/README.md` สำหรับผู้ใช้: ติดตั้ง, `.env`, run, ภาพหน้าจอ และ disclaimer ว่าไม่ใช่คำแนะนำการลงทุนและไม่ส่งคำสั่งซื้อขาย
- `docs/architecture.md`: แผนภาพ layer (harness/domain), flow ของ run/event และตาราง pillar → ไฟล์ที่ implement จริง
- `docs/adding-a-provider.md`, `docs/adding-a-skill.md`, `docs/adding-a-workflow.md` (ทีละขั้นพร้อมตัวอย่างจากโค้ดจริง)

## Definition of Done
- [ ] `evals.run --sample 10` รันจบและได้รายงาน โดย citation coverage ≥ 95% และ unverified = 0 ใน ≥ 90% ของข้อ
- [ ] E2E ผ่านทั้ง 4 scenario โดยไม่ใช้ network
- [ ] look-ahead audit ผ่าน
- [ ] เอกสารครบตาม 14.6

## Follow-ups (หลังแผนนี้)
Playwright browser tool, earnings call transcripts, SET official data, multi-portfolio, การแจ้งเตือนตามกำหนดเวลา (cron แบบ Dexter `src/cron/`), การต่อ broker แบบ paper trading (ต้องมี gate เพิ่มและผู้ใช้ต้องอนุมัติชัดเจน)
