# Phase 10 — Trading workflow backend (T1–T5, F1, F3)

## เป้าหมาย
สร้าง workflow `trading` สำหรับ symbol เดียว (หุ้น US/ไทย, forex, crypto, ทองคำ):

```
resolve_symbol → load_context → [analysts: market, news, fundamentals*, sentiment, macro*] (ขนานกัน)
→ bull ⇄ bear (N รอบ) → research_manager (structured) → trader (structured + compute sizing)
→ aggressive ⇄ conservative ⇄ neutral (M รอบ) → portfolio_manager (structured Decision)
→ policy_gate → approval (interrupt) → record_decision → report
```

โดยทุกตัวเลขที่ใช้ตัดสินใจ (entry, stop, target, size, R:R) ต้อง compute ทั้งหมด

## ต้องเสร็จก่อน
P09

## อ่านก่อนเริ่ม (ทั้งไฟล์)
- `../TradingAgents/tradingagents/agents/analysts/{market,news,fundamentals,sentiment}_analyst.py`
- `../TradingAgents/tradingagents/agents/researchers/{bull,bear}_researcher.py`
- `../TradingAgents/tradingagents/agents/managers/{research_manager,portfolio_manager}.py`
- `../TradingAgents/tradingagents/agents/trader/trader.py`
- `../TradingAgents/tradingagents/agents/risk_mgmt/{aggressive,conservative,neutral}_debator.py`
- `../TradingAgents/tradingagents/agents/{state,schemas,structured,rating,context,tools}.py`
- `../TradingAgents/tradingagents/graph/{setup,conditional_logic,analyst_execution,propagation,reflection,settlement}.py`
- `../TradingAgents/tradingagents/{decision_log,portfolio,reporting,default_config}.py`
- `../TradingAgents/tradingagents/dataflows/vendors/yahoo/snapshot.py`

## Tasks

### 10.1 Input / State (`app/domain/workflows/trading/`)
- `TradeRunInput`: `symbol: str`, `analysis_date: date = today`, `analysts: list[Literal["market","news","fundamentals","sentiment","macro"]]`, `debate_rounds: int = 1 (1–3)`, `risk_rounds: int = 1 (1–3)`, `use_portfolio: bool = True`, `horizon: Literal["intraday","swing","position"] = "swing"`, `language: "th"|"en" = "th"`
- default analysts: stock ใช้ market, news, fundamentals, sentiment ส่วน forex/commodity ใช้ market, news, macro (ตัด fundamentals อัตโนมัติ) และ crypto ใช้ market, news, sentiment
- `signature_fields = ["analysts", "debate_rounds", "risk_rounds", "use_portfolio", "horizon"]`
- `TradingState` (TypedDict) port `AgentState` + `InvestDebateState` + `RiskDebateState` พร้อมเจ้าของ field:

| field | writer | reducer |
|-------|--------|---------|
| `instrument` (Symbol + identity) | resolve_symbol | replace |
| `portfolio_ctx` (PortfolioContext 3 สถานะ) | load_context | replace |
| `market_snapshot` (compute verified) | load_context | replace |
| `past_lessons` (จาก decision log ของ symbol นี้) | load_context | replace |
| `reports` dict[analyst → AnalystReport] | analysts | merge |
| `invest_debate` | bull, bear, research_manager | replace (node เขียนทั้ง object ใหม่) |
| `research_plan` (ResearchPlan) | research_manager | replace |
| `trade_proposal` (TraderProposal + computed sizing) | trader | replace |
| `risk_debate` | aggressive, conservative, neutral, portfolio_manager | replace |
| `final_decision` (PortfolioDecision) | portfolio_manager | replace |
| `policy` (PolicyEvaluation) | policy_gate | replace |
| `approval` | approval | replace |
| `report_md` | report | replace |

### 10.2 Nodes
**ทุก LLM node:**
- prompt ในไฟล์ `app/domain/agents/prompts/trading/<role>.v1.md` (port จาก TradingAgents แล้วปรับให้เข้ากับตลาดไทย/forex และภาษา output)
- บอก `NO_EXTERNAL_TOOLS` ถ้าเป็น structured node
- ใส่ `market_snapshot` เป็น ground truth ของตัวเลขราคา (ตามแบบ snapshot.py #830)

| node | kind | tier | tools | รายละเอียด |
|------|------|------|-------|-----------|
| `resolve_symbol` | compute | – | – | `normalize_symbol` + identity (ชื่อบริษัท, ตลาด, สกุลเงิน) port `resolve_instrument_identity` (context.py) |
| `load_context` | compute | – | – | `verified_snapshot` ณ analysis_date, portfolio_ctx (จาก P12 ถ้ามี ไม่งั้นเป็น `not_provided`) และบทเรียนจาก decision log |
| `market_analyst` | agent | quick | get_prices, get_market_snapshot, compute_indicators | port market_analyst.py ให้เลือก indicator ≤ 8 ตัวที่เกี่ยวข้อง แล้วเขียนรายงาน + ตาราง |
| `news_analyst` | agent | quick | get_news, get_global_news, get_macro, get_prediction_markets | port news_analyst.py |
| `fundamentals_analyst` | agent | quick | get_fundamentals, get_statements, get_insider_activity, compute_comps | port fundamentals_analyst.py (ข้ามถ้า asset ไม่ใช่ stock) |
| `sentiment_analyst` | agent | quick | get_social_sentiment, get_news | port sentiment_analyst.py และบอกชัดเมื่อแหล่ง unavailable |
| `macro_analyst` | agent | quick | get_macro, get_global_news, get_prediction_markets | ใหม่ สำหรับ forex/commodity (rate differential, DXY, risk sentiment) |
| `bull_researcher` / `bear_researcher` | agent | deep | – | port พร้อม `past_lessons` และ router `should_continue_debate` (count ≥ 2×rounds → research_manager) |
| `research_manager` | agent | deep | – | structured `ResearchPlan{stance: Rating, thesis, key_risks, catalysts, decision: Decision}` |
| `trader` | agent + compute | deep | compute_returns_risk | structured `TraderProposal{side: long/short/flat, entry_zone, stop_basis: "atr"|"level", atr_multiple?, stop_level?, target_levels, horizon, rationale, decision}` โดย LLM **เสนอ basis** แล้ว node คำนวณ `stop_price`, `targets`, `risk_reward`, `position_size` (จาก equity ของพอร์ต หรือ notional สมมติ 1,000,000 THB ถ้าไม่มีพอร์ต) ผ่าน `domain/compute/risk.py` แล้วเขียนลง proposal พร้อม provenance |
| `aggressive` / `conservative` / `neutral` | agent | quick | – | port debators พร้อม router `should_continue_risk_analysis` (count ≥ 3×rounds → PM) |
| `portfolio_manager` | agent | deep | – | structured `PortfolioDecision{rating: Rating, decision: Decision, final_size_pct, adjustments, summary}` โดย size ที่ปรับต้องคำนวณใหม่ผ่าน compute |
| `policy_gate` | gate | – | – | PolicySet: ValidSymbol, RequireStopLoss, MaxRiskPerTrade, MaxPositionWeight (หลังรวมกับพอร์ต), CashSufficient, MinConfidence |
| `approval` | approval | – | – | interrupt พร้อม proposal + policy + provenance |
| `record_decision` | compute | – | – | idempotent=False: บันทึก decision log (port `TradingMemoryLog`: append-only markdown + DB row) **ไม่ส่ง order** |
| `report` | report | quick | – | รวมรายงานเป็น markdown (port reporting.py) |

**Routers** (ใช้ path map ครบตาม P09):
- หลัง analysts → bull
- debate router
- หลัง research_manager: `route_decision(plan.decision)` → REJECT (ไม่ควรเทรด) → `report` (ข้าม trader/risk และบันทึกเหตุผล) ส่วนอื่นไป trader
- risk router
- หลัง PM: REJECT → report / อื่นๆ → policy_gate
- หลัง policy: blocked → trader (1 ครั้ง, เก็บ counter ใน state) → ถ้ายัง blocked ให้ report พร้อมสถานะ REJECT / ผ่าน → approval
- หลัง approval: approved|edited → record_decision → report / rejected → report

**Analysts ขนาน:** fan-out จาก `load_context` ไปยัง analysts ที่เลือก แล้ว join ก่อน bull (ใช้ `analyst_execution.py` เป็นแนวทางในการสร้างแผนตาม analysts ที่เลือก) และ analyst แต่ละตัวมี agent loop ของตัวเองที่ใช้ executor + guards (max_iterations 6)

### 10.3 Structured schemas (`app/domain/agents/schemas.py`)
port `schemas.py` ให้ครบ + `field_validator(mode="before")` ด้วย coerce จาก P04 และ render เป็น markdown (`render_*`) เพื่อใช้ใน report และ UI

### 10.4 Decision log + reflection (optional ใน phase นี้ ถ้าเวลาพอ)
- `record_decision` เขียน `DECISION:` entry ตามแบบ decision_log.py
- `settle_decisions()` (สั่งจาก CLI/cron ภายหลัง): หลังผ่าน `holding_period_days=5` ให้คำนวณ return เทียบ benchmark (`.BK` → `^SET.BK`, US → `SPY`, forex → ไม่มี benchmark) ด้วย compute แล้วให้ quick LLM เขียน reflection 2–4 ประโยคตาม `reflection.py` และเก็บเป็น `REFLECTION:` เพื่อให้ `load_context` ของรอบถัดไปนำไปใช้เป็น `past_lessons`

### 10.5 API (`app/api/routes/trade.py`)
- `POST /api/trade/runs` → `{run_id, thread_id}` (scope = symbol canonical)
- `GET /api/trade/runs?symbol=` → history `{run_id, symbol, date, rating, verdict, status, created_at}`
- `GET /api/trade/runs/{id}` → `{run, input, graph_view, nodes: {id: {status, started_at, finished_at, summary, report_md?, structured?}}, decision?, policy?, approval?}`
- ใช้ endpoint resume/approvals/state จาก P09

### 10.6 Tests (FakeLLM ทั้งหมด)
- happy path: stock ใช้ 4 analysts, 1 รอบ debate แล้ว event ของทุก node ตามลำดับ, analysts รันขนาน (timestamps ซ้อนกัน) และจบที่ approval จากนั้น approve → record → report
- forex: fundamentals ถูกตัดและ macro ถูกใช้
- research_manager REJECT → ข้าม trader แล้วไป report
- PM parse fail → REVIEW → policy → approval (ไม่ใช่ APPROVE)
- policy block (ไม่มี stop) → trader แก้ 1 ครั้ง → ผ่าน / ไม่ผ่าน → REJECT
- ตัวเลข sizing ใน proposal ต้องตรงกับ compute (LLM ใส่ size มั่วแล้ว node ต้อง overwrite และบันทึก warning)
- crash ที่ trader แล้ว resume → analysts ไม่รันซ้ำ
- path map ครบ: fuzz ค่า `latest_speaker` แปลกๆ แล้วต้องไปที่ fallback โดยไม่ crash
- live (`-m live`): PTT และ EURUSD ครบ pipeline ภายในงบ $0.50

## Definition of Done
- [ ] `POST /api/trade/runs {"symbol":"PTT"}` จบที่ waiting_approval และ approve แล้วได้ report
- [ ] ทุกตัวเลขใน decision card (entry, stop, target, size, R:R) มี provenance ที่ `kind=compute`
- [ ] forex/ทองคำ/crypto ทำงานได้
- [ ] test ผ่านทั้งหมด

## นอกขอบเขต
UI (P11) และการส่ง order ไป broker (ห้ามทำ)
