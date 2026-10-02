# Phase 12 — Portfolio backend + AI advisor (F1, T4, T5, D2 memory)

## เป้าหมาย
- เก็บพอร์ตของผู้ใช้เป็น persistent memory: position, จำนวน, ต้นทุน, % น้ำหนัก และเงินสด
- คำนวณ analytics ทั้งหมดด้วย compute
- มี workflow `portfolio_advisor` ที่ให้ AI วิเคราะห์และเสนอ rebalance แบบ structured → policy gate → human approval → บันทึกแผน
- ให้ trading workflow (P10) ใช้พอร์ตนี้เป็น `portfolio_ctx`

## ต้องเสร็จก่อน
P09 (และ P10 สำหรับการเชื่อม portfolio_ctx)

## อ่านก่อนเริ่ม
- `docs/contracts.md` §5 (Portfolio), §8, §9
- `../TradingAgents/tradingagents/portfolio.py` (**ทั้งไฟล์**: 3 สถานะ, broker-neutral, hashing ของ portfolio สำหรับ signature)
- `../TradingAgents/tradingagents/agents/managers/portfolio_manager.py`
- `../FinRobot/finrobot_equity/core/src/modules/sensitivity_analyzer.py`, `catalyst_analyzer.py` (แนวคิดการวิเคราะห์)
- `app/domain/compute/portfolio.py`, `returns.py`, `risk.py` (P01)

## Tasks

### 12.1 Persistence (`app/harness/storage/` + repository)
- ตาราง `portfolios(id, name, base_currency, cash, updated_at)` + `positions(id, portfolio_id, symbol_canonical, raw, quantity, avg_cost, declared_weight, target_weight, note)` และ `plans(id, portfolio_id, run_id, status, proposal json, approved_at, response json)`
- MVP รองรับพอร์ตเดียว (`pf_default`) แต่ schema รองรับหลายพอร์ต
- ทุกการแก้พอร์ตบันทึก revision (`portfolio_revisions` เก็บ snapshot json + เวลา) เพื่อ audit/provenance (`kind=user_input`)

### 12.2 Analytics service (`app/domain/portfolio/analytics.py`) — ใช้ compute ล้วน
`analyze(portfolio, *, as_of, router) -> PortfolioAnalytics`
1. ดึงราคาล่าสุด (QUOTE) ของทุก symbol และ FX เป็น base currency (`USDTHB=X` ฯลฯ) ผ่าน router ด้วย fanout ขนาน
2. ดึงราคาย้อนหลัง 1 ปี (PRICES) ของทุก symbol + benchmark (`^SET.BK` สำหรับ base THB, `SPY` สำหรับ USD)
3. compute: market values, weights (รวม cash), unrealized P&L ต่อตัวและรวม, P&L วันนี้ (ใช้ close ก่อนหน้า), concentration (HHI, top1, top3, effective N) และ exposure ตาม sector/asset_type/currency/market (sector มาจาก PROFILE และ static CSV ของไทย)
4. risk: portfolio returns (ใช้ weights ปัจจุบัน), annualized vol, max drawdown, beta vs benchmark, correlation matrix (≤ 30 ตัว), historical VaR/CVaR 95% และ contribution to risk ต่อตัว
5. กรณี declared weights only: ข้ามมูลค่าและ P&L ใช้ weights ที่กรอกคำนวณ risk และใส่ flag
- คืน `PortfolioAnalytics` ที่ทุกตัวเลขเป็น `Provenanced` และมี `warnings` (เช่น symbol ที่ดึงราคาไม่ได้หรือ FX ขาด)

### 12.3 Advisor workflow (`app/domain/workflows/portfolio_advisor.py`)
Input: `{goal?: str, risk_profile: "conservative"|"balanced"|"aggressive", constraints?: {max_weight?, max_sector?, min_cash?, no_sell?: [symbols], lot_size_th=100}}` และ signature = risk_profile + constraints hash + portfolio hash (ตามแบบ portfolio.py)

```
load_portfolio → compute_analytics → [diagnose: risk_officer, allocation_strategist] (ขนาน, agent loop, ใช้ tools research ได้)
→ propose_rebalance (deep, structured RebalanceProposal)
→ compute_trades (compute: rebalance_trades ตาม lot, cash หลังเทรด, ค่า analytics ใหม่หลัง rebalance)
→ policy_gate → approval → save_plan → report
```

- `RebalanceProposal{targets: [{symbol, target_weight, rationale}], new_symbols?: [...], cash_target, decision: Decision, summary}` โดย LLM เสนอเฉพาะ **target weights + เหตุผล** ส่วนจำนวนหุ้น เงินที่ใช้ และ metric หลังปรับ ให้ `compute_trades` คำนวณทั้งหมด
- ตรวจ target weights รวม + cash = 1 ± 0.001 ถ้าไม่เท่าให้ normalize แล้วบันทึก warning (ห้ามเงียบ)
- `new_symbols` ต้องผ่าน `normalize_symbol` + ดึงราคาได้ ไม่งั้นตัดออกและบันทึก warning
- policy: MaxPositionWeight, MaxSectorWeight, CashSufficient/min_cash, NoSellList, MaxTurnover (เช่น 30%), MinConfidence
- `save_plan` (idempotent=False): บันทึก plan status=approved **ไม่แก้ positions อัตโนมัติ** ผู้ใช้ต้องกด "Apply to portfolio" เองใน UI ซึ่งเป็น action แยกที่บันทึก revision
- `report`: markdown สรุปการวินิจฉัย + ตาราง current → target + metric ก่อน/หลัง

**Quick actions** (ปุ่มใน UI) = preset input ของ workflow หรือ research skill:
- "ตรวจความเสี่ยงพอร์ต" ใช้ skill `portfolio-risk-review` (ใหม่ ใน `domain/skills/analysis/`) แบบไม่เสนอ rebalance
- "เสนอ rebalance" ใช้ advisor workflow
- "หาจุดอ่อน" ใช้ skill `portfolio-weakness` (ดูตัวที่ contribution to risk สูงแต่ fundamentals อ่อน)
- Portfolio chat: session `kind=portfolio` ใช้ research agent loop + context ของพอร์ต (analytics summary ใน system prompt) + tool `get_portfolio_analytics`

### 12.4 เชื่อมกับ Trading (P10)
- `load_context` ของ trading อ่านพอร์ตจริงจาก repository แล้วสร้าง `PortfolioContext` และ `policy_gate` ใช้ weights หลังรวม position ใหม่
- trade run input `use_portfolio=false` → `not_provided`

### 12.5 API (`app/api/routes/portfolio.py`)
- `GET /api/portfolio` / `PUT /api/portfolio` (validate: symbol resolve ได้ หรือคืน 422 พร้อมรายการที่ผิด, quantity ≠ 0, declared weights รวม ≤ 1)
- `POST /api/portfolio/import` (CSV: `symbol,quantity,avg_cost,weight,note` โดยคอลัมน์ weight ใช้แทน quantity ได้) คืน preview + errors ก่อนแล้วค่อย commit ด้วย `?commit=1`
- `GET /api/portfolio/analytics` (cache 60s ตาม hash ของพอร์ต)
- `POST /api/portfolio/advise` → `{run_id}`
- `GET /api/portfolio/plans`, `POST /api/portfolio/plans/{id}/apply` (บันทึก revision)
- `GET /api/portfolio/revisions`

### 12.6 Tests
- analytics: พอร์ตผสม TH + US + เงินสด THB แล้ว FX ถูกต้อง, weights รวม 1 และ P&L ตรงกับที่คำนวณมือ (fixture ราคา)
- declared-only mode
- symbol ดึงราคาไม่ได้ → warning และ weight ของตัวนั้นไม่ถูกเดา
- advisor (FakeLLM): LLM เสนอ weights รวม 1.07 → normalize + warning, compute_trades ปัด lot 100, policy MaxPositionWeight block → กลับ propose 1 ครั้ง, approve → plan saved แต่ positions ไม่เปลี่ยน, apply → revision ใหม่
- trading ใช้ portfolio_ctx ที่ถูกต้องทั้ง 3 สถานะ
- CSV import: ผิดรูปแบบ, symbol ไม่รู้จัก และ weight + quantity ปนกัน

### 12.7 คำขอเพิ่มจากผู้ใช้ (2026-09-26) — แทนที่ข้อจำกัด "MVP พอร์ตเดียว"
อ้างอิงหน้าตาแอป Dime! (สินทรัพย์ของฉัน: แยกสินทรัพย์ / แยกพอร์ต)
- **หลายพอร์ต**: CRUD พอร์ต (ชื่อ, ไอคอน/สี, base currency) และมุมมองรวมทุกพอร์ต (`GET /api/portfolios`, `/api/portfolios/{id}`, `/api/portfolios/summary`) ทั้งแบบแยกพอร์ตและแยกสินทรัพย์ (รวม symbol เดียวกันข้ามพอร์ต, กรองตามตลาด ไทย/สหรัฐฯ)
- **กรอกแบบ "มูลค่า + % กำไรขาดทุน"** ต่อหุ้น (เช่น NVDA 549.96 USD, +14.52%) เป็นโหมดหลัก นอกเหนือจาก qty/avg cost:
  compute ตอนบันทึก: ดึงราคาล่าสุด → `units = value / price`, `cost_basis = value / (1 + pnl%)` แล้วเก็บ units/cost (provenance `user_input` + `compute`) เพื่อให้มูลค่า/กำไรเดินตามราคาตลาดได้ต่อ
- **Job รายสัปดาห์ ทุกวันศุกร์ 03:00 Asia/Bangkok**: อัปเดตราคา/มูลค่า/% กำไรทุกพอร์ต (snapshot เก็บเป็น history) แล้วรัน AI วิเคราะห์พอร์ต (skill `portfolio-risk-review`) บันทึกเป็นรายงานรายสัปดาห์ — ไม่ส่ง order, ไม่แก้ positions; ต้องมี scheduler ใน backend (lifespan) + ปุ่ม "รันตอนนี้" และบันทึก run ปกติ (ดูใน UI ได้); ถ้าเครื่องปิดตอนถึงเวลาให้รันทันทีเมื่อ backend เปิด (catch-up)
- **Research chat: toggle "ใช้ข้อมูลพอร์ต"**: `PostMessage.portfolio_ids` (ว่าง = ไม่ใช้) → research workflow ใส่ snapshot ของพอร์ตที่เลือก (holdings, weights, P&L จาก analytics) ใน context + tool `get_portfolio_analytics` จำกัดเฉพาะพอร์ตที่เลือก

## Definition of Done
- [x] PUT พอร์ต (PTT 1000 หุ้น, AAPL 10, เงินสด 50,000 THB) แล้ว GET analytics ได้ weights, P&L, risk พร้อม provenance ทุกตัว
- [x] `POST /portfolio/advise` จบที่ approval และ approve แล้วได้ plan
- [x] trading run ของ PTT เห็น portfolio_ctx=positions และ policy คิด weight รวม
- [x] test ผ่าน

> ตรวจแล้ว 2026-09-26 บน live server (yfinance + groq/ThaiLLM, data dir ชั่วคราว) — ข้อจำกัดที่พบ: Yahoo คืนราคาย้อนหลัง `^SET.BK` แค่ 1 แท่ง จึงคำนวณ beta ของพอร์ต THB ไม่ได้ (มี warning `beta_unavailable`)

## นอกขอบเขต
UI (P13) และการเชื่อม broker
