# Finance Harness — System Architecture

เอกสารนี้อธิบายระบบทั้งหมดของ `Product/` ว่าทำงานอย่างไร ประกอบด้วยอะไรบ้าง และใช้ tech stack อะไร
ใช้คู่กับ [contracts.md](contracts.md) (interface) · [data-sources.md](data-sources.md) (แหล่งข้อมูล) · [ui-spec.md](ui-spec.md) (UI)

> ระบบนี้เป็นเครื่องมือวิเคราะห์และให้คำแนะนำ **ไม่ส่งคำสั่งซื้อขายจริง** คำว่า "execute" หมายถึงบันทึกและนำเสนอแผนที่ผ่าน approval แล้วเท่านั้น

**Workflow รวม:** User → Next.js UI → FastAPI → Workflow (LangGraph) → Agent loop + Tools + LLM → Policy gate → Human approval → Report | Events / Provenance / Scratchpad ครอบคลุมทั้งระบบ

---

## 1. ภาพรวม (Big Picture)

```
                         ┌───────────────────────────────────────────────┐
  ① User                 │  ② FRONTEND  Next.js 15 · :3000                │
  (Browser)  ──HTTP──▶   │  /research   /portfolio   /trade   /backtest   │
                         │  /memory   /settings                           │
                         │  Zustand store ◀── run-reducer (idempotent)    │
                         └───────┬─────────────────────────▲─────────────┘
                       REST/JSON │                         │ SSE (replay ด้วย Last-Event-ID)
                                 ▼                         │
┌────────────────────────────────────────────────────────────────────────────────────┐
│ ③ BACKEND  FastAPI · :8000      app/api/routes                                     │
│   /api/sessions  /api/trade/runs  /api/portfolio*  /api/backtests*  /api/settings  │
│   /api/runs/{id}/events|resume|state   /api/approvals/{id}   /api/market/{s}/…     │
│   /api/memory*  /api/reasoning-bank*   (header X-User-Id, default usr_default)     │
└───────────────────────────────────┬────────────────────────────────────────────────┘
                                    │ start run
                                    ▼
┌────────────────────────────── HARNESS (app/harness) ───────────────────────────────┐
│ ไม่รู้จักคำว่า "หุ้น" — orchestration, state, safety เท่านั้น                         │
│                                                                                    │
│  graph/      LangGraph adapter: compile workflow spec → StateGraph + checkpoint    │
│  workflow/   Workflow base + registry                                              │
│  runtime/    agent loop · limits · loop detection · retry · timeout                │
│  context/    microcompact + full compact + token budget                           │
│  executor/   validate → permission → execute → normalize                           │
│  gates/      policy runner + human approval (interrupt)                            │
│  structured/ structured output + fallback + coercion                               │
│  llm/        provider interface · factory · failover · claude_code                 │
│  state/      conversation · scratchpad (JSONL) · memory (SQLite KV)                │
│  memory/     user memory store (FTS5 trigram) · MemoryEngine · ReasoningBank (P17) │
│  provenance/ record + store + number-check                                         │
│  events/     event bus + SSE replay        storage/  SQLAlchemy repos              │
│  skills/     SKILL.md loader + registry    config/   settings override             │
└───────────────┬───────────────────────────────────────────────────┬────────────────┘
                │ interfaces: Tool · Skill · Workflow · DataProvider │
                ▼                                                    ▼
┌────────────────────────── DOMAIN (app/domain) ─────────────┐   ┌──── ภายนอก ────────┐
│ ห้าม import langgraph / langchain / fastapi                 │   │ ⑤ LLM providers    │
│  workflows/  research · trading · portfolio_advisor ·       │   │ ⑤ Data providers   │
│              backtest                                       │   │ ⑤ Claude Code CLI  │
│  agents/     role prompts (versioned) + output schemas      │   └────────────────────┘
│  tools/      primitives ที่ห่อ compute/ และ data/            │
│  skills/     SKILL.md (research/analysis/…)                 │
│  compute/    pure functions (Decimal) — ตัวเลขทุกตัว          │
│  data/       DataProvider + providers/ + DataRouter         │
│  policy/     deterministic risk rules                       │
│  portfolio/  analytics · entry · apply                      │
│  memory/     schemas · ports · profile · scrub · discovery  │
│              (5 Whys) · prompts/*.v1.md (P17)               │
│  models/     Symbol · Position · Decision · Provenanced     │
└────────────────────────────────────────────────────────────┘
                │
                ▼
      ④ Storage:  SQLite app.db · scratchpad JSONL · checkpoint DB ต่อ symbol
```

หลักคิดข้อแรก (F2): **Harness ≠ Domain** ถ้าเปลี่ยน LangGraph เป็น framework อื่น ต้องแก้แค่ `app/harness/graph/` เท่านั้น ตรวจด้วย `import-linter` (`uv run lint-imports`)

---

## 2. ขั้นตอนการทำงานแบบ end-to-end

| # | ขั้นตอน | รายละเอียด |
|---|---------|-----------|
| 1 | **User → UI** | ผู้ใช้เปิดหน้า `/research`, `/portfolio`, `/trade` หรือ `/backtest` ที่ `localhost:3000` |
| 2 | **UI → API** | ส่งคำขอ (คำถาม / symbol / พอร์ต) ไป FastAPI ได้ `run_id` กลับมาทันที |
| 3 | **Subscribe SSE** | UI เปิด `GET /api/runs/{id}/events` ฟัง event แบบเรียลไทม์ ถ้าหลุดจะ reconnect ด้วย `Last-Event-ID` แล้ว backend replay event ที่ขาดจาก DB |
| 4 | **Workflow เริ่ม** | Workflow registry หา workflow ตามชนิดงาน → `harness/graph` compile เป็น LangGraph แล้วรันทีละ node |
| 5 | **Node ทำงาน** | node ที่ใช้ LLM วนใน agent loop (LLM ขอ tool → executor รัน → ผลกลับไปหา LLM) ส่วนตัวเลขทุกตัวมาจาก `compute/` |
| 6 | **Checkpoint** | หลังทุก node บันทึก checkpoint ถ้า crash สามารถ `POST /runs/{id}/resume` ต่อจาก node ที่ล้ม |
| 7 | **Policy gate** | ข้อเสนอ (เทรด / ปรับพอร์ต) ผ่าน rule ใน `domain/policy` ถ้าผิดกฎจะส่งกลับไปแก้ 1 ครั้ง แล้ว reject |
| 8 | **Human approval** | graph หยุดด้วย `interrupt()` ส่ง `approval_required` ไป UI ผู้ใช้เลือก approve / reject / edit |
| 9 | **Report** | สร้างรายงานสุดท้ายด้วย LLM call แยกที่ไม่ bind tool จาก scratchpad + provenance แล้ว flag ตัวเลขที่จับคู่ provenance ไม่ได้ |
| 10 | **แสดงผล** | UI แสดง pipeline, tool call, debate, decision card, citation ทุกตัวเลข (hover = แหล่งที่มา) |

---

## 3. ฟีเจอร์ 3 ตัว และ Workflow ของแต่ละตัว

### 3.1 Research Chat — `/research`

ChatBot สำหรับ deep research เลือก scenario ได้ (earnings review, macro outlook, sector scan, company valuation ฯลฯ) และเปิดใช้ข้อมูลพอร์ตได้

```
plan ─▶ recall_memory ─▶ assess_discovery ─┬─ ask_only          ─▶ write_question ───────────────┐
                                            ├─ research          ─▶ retrieve_analyze ─▶ verify    ├─▶ learn
                                            └─ research_then_ask ─▶   ─▶ synthesize_report ───────┘
                          (agent loop: tool ≤10 รอบ · expert roles 7 บทบาท · number check กับ provenance)
```

- **research v2 (P17):** `recall_memory` ฉีด INVESTOR PROFILE + บทเรียน ReasoningBank ≤ 2 เข้า system prompt · `assess_discovery` (5 Whys, quick structured) เลือกว่าจะถามอย่างเดียว / ตอบอย่างเดียว / ตอบแล้วปิดท้ายด้วยคำถาม "ทำไม" 1 ข้อ (≤ 5 ต่อเป้าหมาย, `skip_discovery` ข้ามได้) · `learn` เขียน memory จากข้อความผู้ใช้ + กลั่นบทเรียนลง ReasoningBank (ห้ามทำให้ run fail) · สถานะ 5 Whys อยู่ใน `session.meta["discovery"]`
- Agent loop: LLM เลือก tool ได้เฉพาะในรอบนี้ (tool ใน `domain/tools` 25 ตัว + memory/skill tools รวม `user_memory_search`)
- Skills 10 ตัว (`SKILL.md`) เป็น scenario ที่ผู้ใช้เลือกล่วงหน้า แต่ละ skill รันได้ครั้งเดียวต่อ query
- Citation สร้างอัตโนมัติจาก provenance ไม่ให้ LLM พิมพ์เอง
- API: `/api/sessions`, `/api/skills`, `/api/workflows/{id}/graph`

### 3.2 Trading Workflow — `/trade`

เลือก symbol เดียว (หุ้นไทย `.BK`, US ticker หรือ forex `EURUSD=X`) แล้วไล่ pipeline 15 nodes

```
resolve ─▶ context ─▶ analysts ─▶ bull ⇄ bear ─▶ research_manager ─▶ trader
                      (market · news ·            (debate N รอบ     (TraderProposal)
                       fundamentals* · sentiment   ตาม config)
                       รันพร้อมกันใน node เดียว)
   ─▶ aggressive ─▶ conservative ─▶ neutral ─▶ portfolio_manager
      (risk debate)                              (PortfolioDecision)
   ─▶ policy_gate ─▶ approval ─▶ record_decision ─▶ report
```

\* forex ไม่มี fundamentals ของบริษัท ใช้ macro/rates แทน

- ตัวเลขทุกตัวใน decision card (size, stop, target, R:R) มาจาก internal tool `size_trade` / `trade_snapshot` (`kind=compute`) LLM เสนอได้แค่ "stop basis"
- Router อ่านเฉพาะ enum/ตัวเลขที่ parse แล้ว ถ้า parse ไม่ได้หรือ confidence ต่ำ → `REVIEW` ห้ามเป็น `APPROVE`
- Policy block → trader แก้ 1 ครั้ง → ถ้ายังผิด → `REJECT`
- Approval แบบ `edited` แก้ได้ size / stop / targets แล้ว backend คำนวณ + ตรวจ policy ใหม่
- อ่านพอร์ตจริงแบบ 3 สถานะ (มี position / พอร์ตว่าง / ไม่ได้ให้ข้อมูล) เพื่อใช้ weight รวมใน policy
- Checkpoint: scope = symbol, `thread_id = hash(symbol, date, graph_signature)`, ลบ checkpoint เมื่อ run สำเร็จ
- Decision log บน MemoryStore (`trade_decisions`) · API `/api/trade/runs`

### 3.3 Portfolio — `/portfolio`

ผู้ใช้กรอกพอร์ต แล้ว AI วิเคราะห์และเสนอแผนปรับพอร์ต (ไม่แก้ positions จนกว่าผู้ใช้กด apply)

```
load ─▶ analytics ─▶ recall_memory ─▶ diagnose ∥ 2 roles ─▶ propose (RebalanceProposal)
     ─▶ rebalance_plan (lot 100) ─▶ policy (retry 1) ─▶ approval (edit target %) ─▶ save_plan ─▶ report ─▶ learn
```

- **portfolio_advisor v2 (P17):** ไม่ใส่ goal → ใช้ goal จาก memory (report บอกว่า "Goal from your memory") · approve/edit → บทเรียน `success` ลง ReasoningBank, reject + comment → `pitfall` + memory จาก comment · ทุกคำตอบของคน → `settle` success rate ของบทเรียนที่ใช้

- **การกรอกพอร์ต 7 โหมด:** `qty_cost`, `qty_pnl_pct`, `qty_pnl_amount`, `value_pnl_pct`, `value_pnl_amount`, `cost_total`, `weight` + วางข้อความจากแอปโบรก (`parse-holdings` → quick LLM structured) + นำเข้า CSV
- **Analytics (`domain/portfolio/analytics.py`):** values, weights, P&L, day P&L, HHI, exposure, volatility, MDD, beta, VaR/CVaR, correlation, risk contribution ทุกตัว `Provenanced` ตัวไหนไม่มีราคาจะขึ้น warning ไม่เดา weight
- ทุกการแก้ไข = revision + provenance `user_input` · มีสรุปรายสัปดาห์ (ศุกร์ 03:00 BKK + catch-up)
- ภาพรวมหลายพอร์ต: แยกสินทรัพย์ / แยกพอร์ต / ตลาดไทย-สหรัฐฯ / USD ≈ THB

### 3.4 Backtest — `/backtest`

รัน trading workflow ย้อนหลังเป็น "cell" ต่อวัน บนข้อมูล point-in-time (`PointInTimeRouter`) แล้วจำลองพอร์ตเทียบ buy & hold และ SMA cross

- cell = trading run ลูกที่ auto-approve, cache ข้าม backtest, มี budget, resume ได้
- `domain/compute/backtest/` (simulate, performance, buy_and_hold, sma_cross)
- API `/api/backtests*` (estimate / start / detail / simulate / delete)

### 3.5 Memory — `/memory` (P17)

AI จำผู้ใช้ข้าม session และเก่งขึ้นจากทุก run — ผู้ใช้เห็น แก้ และลบได้ทั้งหมด

- **User memory** (`harness/memory/engine.py`, pattern Mem0): extract (quick LLM, เฉพาะข้อความผู้ใช้) → หา neighbour (key เดียวกัน หรือ FTS5 trigram ที่ค้นไทยได้) → guard แบบ deterministic ก่อน LLM (key ชนกัน = SUPERSEDE, inferred ห้ามทับสิ่งที่ผู้ใช้บอกเอง, risk/goal/constraint ที่อนุมาน = candidate) → reconcile `ADD/UPDATE/SUPERSEDE/RETIRE/NOOP` · ไม่แก้ in-place: ขัดแย้ง = แถวใหม่ + แถวเก่า `superseded` (`valid_to`) ดูประวัติได้ · ลบจริงเฉพาะ "ลืม" (`DELETE /memory/{id}?cascade=1`)
- **ReasoningBank** (`harness/memory/bank.py`): run ที่ตัดสินผลได้ → กลั่นบทเรียน title/description/content → `scrub_text` (PII = ทิ้ง, จำนวนเงิน → bucket, ticker → "sector large-cap") → privacy judge → dedupe → bank กลาง · retrieve ด้วย FTS × (0.5 + success_rate) · success rate แบบ Bayesian อัปเดตจากผลจริง, retire อัตโนมัติเมื่อแย่
- หน้า `/memory`: โปรไฟล์ที่ถูกฉีดจริง + token, รายการตามหมวด (ยืนยัน/แก้/เลิกใช้/ลืม/ประวัติ), เพิ่มเอง, แท็บ ReasoningBank · chat มี MemoryChip, DiscoveryCard (n/5 + ปุ่มข้าม), inspector tab Memory

---

## 4. Tech Stack

### 4.1 Backend

| ชั้น | เทคโนโลยี | ใช้ทำอะไร |
|------|-----------|-----------|
| ภาษา / package | Python 3.12, `uv` | จัดการ environment และ dependency |
| Web framework | **FastAPI**, `sse-starlette`, uvicorn | REST + SSE streaming |
| Orchestration | **LangGraph** + `langgraph-checkpoint-sqlite` | compile workflow เป็น StateGraph, checkpoint, `interrupt()` |
| LLM adapter | LangChain (เฉพาะใน `harness/llm` และ `harness/graph`) | เรียก provider ผ่าน interface เดียว |
| Validation | **Pydantic v2** | schema ทุก boundary (tool args, structured output, API) |
| Database | **SQLAlchemy 2 + Alembic** (SQLite) | พอร์ต, revision, plans, settings, events, memory, backtest |
| HTTP client | httpx | เรียก data provider และ LLM |
| Compute | pandas, `Decimal` | indicator, portfolio math, risk, valuation, SMC, ML |
| Test | pytest, respx, FakeLLM | recorded fixtures ห้ามเรียก API จริง (live test ใช้ `-m live`) |
| Quality | ruff, `mypy --strict`, import-linter | lint, typecheck, บังคับขอบเขต harness/domain |

### 4.2 Frontend

| ชั้น | เทคโนโลยี |
|------|-----------|
| Framework | Next.js 15 (App Router) + React 19 + TypeScript strict |
| Package manager | pnpm |
| Styling / UI | Tailwind v4 + shadcn/ui, ฟอนต์ IBM Plex Sans Thai + JetBrains Mono |
| State | Zustand + `run-reducer` (idempotent, buffer ลำดับ event) |
| Streaming | `@microsoft/fetch-event-source` (SSE + `Last-Event-ID`) |
| Charts | `lightweight-charts` v5 (candle, indicator), Recharts (donut, bar) |
| Pipeline graph | `@xyflow/react` + dagre |
| Table | `@tanstack/react-table` v9 |
| Form | react-hook-form + zod, papaparse (CSV) |
| Test | vitest (component test), Playwright (E2E — ยังไม่มี, อยู่ใน P14) |
| API types | `pnpm gen:api` สร้างจาก `backend/schemas/openapi.json` |

### 4.3 LLM Providers (ลำดับ failover ตั้งได้ใน Settings)

| Provider | หมายเหตุ |
|----------|----------|
| Anthropic, OpenAI, Google AI Studio (Gemini), Grok | cloud API |
| DeepSeek, Groq, OpenRouter | cloud API |
| ThaiLLM | endpoint ตั้งเองผ่าน `THAILLM_BASE_URL` |
| Ollama (local) / Ollama Cloud | โมเดลรันเครื่องเอง / `ollama.com/v1` |
| **`claude_code`** | Claude ผ่าน subscription โดย spawn `claude -p` headless (key ใน `ANTHROPIC_API_SUB_KEY`) ใช้ส่วนตัวเท่านั้น ยังไม่มี streaming |

แยก 2 tier: **`deep`** (reasoning หนัก: manager, PM, debate) กับ **`quick`** (สรุป, compact, parse) ถ้าไม่ตั้ง จะ auto-pick โมเดลแนะนำให้

### 4.4 Data Providers (ต่อ capability, มี failover)

| Capability | Providers (ใน `domain/data/providers/`) |
|------------|------------------------------------------|
| prices / fundamentals | yfinance, Alpha Vantage, Finnhub, FMP, Financial Datasets, spot metals |
| filings | SEC EDGAR, sec-api |
| macro / calendar | FRED, ForexFactory |
| news / search | web search (Exa, Perplexity, Tavily, LangSearch), web fetch, crypto news |
| sentiment | Reddit (API + RSS), StockTwits, X, Adanos, Polymarket |
| symbols | static symbol provider (normalize + validate) |

รายละเอียดลำดับ failover ดู [data-sources.md](data-sources.md)

---

## 5. Harness — ชั้นที่ทำให้ agent "อยู่ได้นาน / อยู่ในกรอบ / กลายเป็นระบบ"

### 5.1 Agent loop และความปลอดภัย (D5)

| กลไก | ค่าเริ่มต้น (`settings.py`) |
|------|-----------------------------|
| Max iterations ต่อ query | `RUN_MAX_ITERATIONS=10` |
| Tool call รวมต่อ run | `RUN_MAX_TOOL_CALLS_TOTAL=40` |
| Loop detection | tool เดียวกันไม่เกิน 3 ครั้ง, args คล้าย ≥ 0.7 → block |
| Tool timeout | `RUN_TOOL_TIMEOUT_S=30` |
| Wall-clock ต่อ run | `RUN_WALL_CLOCK_S=600` |
| Budget | `RUN_MAX_COST_USD=1.0`, `RUN_MAX_OUTPUT_TOKENS_TOTAL=200000` |
| Retry | exponential backoff เฉพาะ error ชั่วคราว (429, 5xx, timeout, connection) ≤ 3 ครั้ง |
| Graph | `GRAPH_RECURSION_LIMIT=100` |

ชน limit → สร้างคำตอบจาก scratchpad พร้อมบอกว่าหยุดเพราะอะไร ไม่ crash · cancel แบบ graceful

### 5.2 Context management (D1)

- **Microcompact (ทุก turn, ไม่ใช้ LLM):** แทน tool result เก่าด้วย `[Old tool result content cleared]` เก็บ 4 ชิ้นล่าสุด เมื่อมีเกิน 8 ชิ้นหรือเกิน 80k tokens
- **Full compact (ใช้ quick LLM):** สรุป session เป็น 9 หัวข้อ เก็บตัวเลขทุกตัวพร้อมแหล่งที่มา
- ใช้ `input_tokens` จริงจาก API เป็นหลัก ล้มเหลว 3 ครั้งติดกันจะหยุด compact และ fallback เป็นการ clear
- ทุกการ compact ส่ง event `context.compaction` และบันทึกลง scratchpad

### 5.3 State 3 ชนิด (D2) — ห้ามปนกัน

| ชนิด | อายุ | Compact? | ที่อยู่ |
|------|------|----------|--------|
| Conversation | 1 session | ได้ | `harness/state/conversation.py` |
| Working state (scratchpad) | 1 run | **ห้าม** append-only | JSONL ต่อ run + graph state |
| Persistent memory | ถาวร | ไม่เกี่ยว | SQLite (`harness/state/memory.py` KV เช่น `trade_decisions`; P17: `harness/memory/` user memory + ReasoningBank) เขียนเฉพาะ step ที่ตั้งใจ (node `learn` หรือผู้ใช้กดเอง) |

Final answer และ provenance สร้างจาก scratchpad ไม่ใช่จาก conversation ที่ถูก compact

### 5.4 Tool executor (D3)

```
LLM tool call ─▶ validate args (Pydantic) ─▶ permission (allow/ask/deny)
              ─▶ execute (timeout, cancel) ─▶ normalize {ok,data,error,truncated,provenance_ids}
              ─▶ append scratchpad + provenance ─▶ กลับไปหา LLM
```

- Tool `read_only` รันพร้อมกันได้สูงสุด 10 · tool ที่มี side effect รันทีละตัวและผ่าน permission
- Error คืนเป็น result ให้ LLM เห็น ไม่โยน exception

### 5.5 Graph engine (T1–T3)

- Workflow ประกาศ nodes/edges/routers เป็นข้อมูลธรรมดา → `compile.py` แปลงเป็น `StateGraph`
- `validate_spec`: ตรวจ node ที่ไปไม่ถึง, router ที่ไม่มี fallback, field ที่หลาย node เขียนโดยไม่มี reducer
- `GraphRunner`: start / resume (crash-resume และ approval-resume ผ่าน `Command(resume=…)`)
- Checkpoint DB แยกต่อ symbol (sanitize ก่อนใช้เป็น path) · การ resume ไม่รัน tool ที่มี side effect ซ้ำ

### 5.6 Gate: Policy + Human approval (T4)

```
Agent เสนอ ─▶ Policy (โค้ด ไม่ใช่ LLM) ─▶ Human approval (interrupt) ─▶ บันทึก/นำเสนอแผน
```

Policy rules ใน `domain/policy/rules.py` เช่น `MaxPositionWeight`, `MaxSectorWeight`, `RequireStopLoss`, `MinConfidence`, `MinCashWeight`, `NoSellList`, `MaxTurnover`, `MinRiskReward` ตั้งค่าได้ใน Settings หรือ `POLICY_*`

### 5.7 Structured output (T5)

ทุก agent ที่ตัดสินใจต้องคืน schema (`TradeResearchPlan`, `TraderProposal`, `PortfolioDecision`, `RebalanceProposal`) ใช้ `with_structured_output` → fallback free-text + parse → coerce ตัวเลข ("N/A" → None, "$1,234" → 1234) · parse ไม่ได้ = `REVIEW`

### 5.8 Provenance (F3)

ทุกตัวเลขเป็น `Provenanced` มี `id, value, unit, source, tool, timestamp, as_of, calculation, inputs, agent, run_id` เก็บใน provenance store · ตัวเลขใน final answer ที่จับคู่ไม่ได้จะถูก flag บน UI · hover ที่ตัวเลข = แหล่งที่มา, คลิก = lineage ใน inspector

### 5.9 LLM ≠ Deterministic compute (F1)

LLM เสนอ assumption (อยู่ในขอบเขตที่ validate) → `domain/compute` คำนวณด้วย `Decimal` → ตัวเลข + provenance → LLM อธิบาย ของที่ต้องคำนวณใน compute เท่านั้น: weight, P&L, return, volatility, drawdown, correlation, indicator (SMA/EMA/RSI/MACD/ATR), position size, stop distance, DCF/WACC/comps, pip value, SMC setup, ML signal

---

## 6. Events และ Streaming (SSE)

- ทุกเหตุการณ์ใน run เป็น event ที่มี `seq` เก็บลง DB (ตาม `contracts.md` §10) เช่น `run.started`, `node.started/finished`, `tool.call/result`, `context.compaction`, `approval_required`, `run.finished`, `backtest.*`, และ P17: `memory.recalled` (ก่อน `llm.*` ของ run), `discovery.assessed`, `memory.updated` (หลัง `answer.final`), `reasoning.distilled` (+ `answer.final.discovery` = ความคืบหน้า 5 Whys)
- `GET /api/runs/{id}/events` ส่ง SSE, รองรับ `Last-Event-ID` → replay ได้เมื่อ refresh หรือ reconnect
- Frontend `run-reducer` ทำให้ event เดิมซ้ำไม่เกิดผลซ้ำ และ buffer เมื่อ event มาไม่เรียง
- Schema: `uv run python -m app.harness.events.export` → `backend/schemas/events.schema.json`

---

## 7. Storage

| ที่เก็บ | ตำแหน่ง | เนื้อหา |
|---------|---------|---------|
| SQLite หลัก | `DATA_DIR/app.db` | runs, events, sessions, portfolios, positions, portfolio_revisions, plans, snapshots, settings, memory (KV), backtests, `user_memory` + `user_memory_fts`, `reasoning_bank` + `reasoning_bank_fts`, `reasoning_bank_uses` (Alembic migration 0001–0006; FTS5 `tokenize='trigram'`) |
| Scratchpad | `DATA_DIR/…/*.jsonl` | working state ต่อ run (append-only) |
| Checkpoint | SQLite แยกต่อ symbol | state ของ LangGraph หลังทุก node |
| Provenance | ใน `app.db` | record ของทุกตัวเลข |

---

## 8. UI (Frontend)

| หน้า | เนื้อหา |
|------|---------|
| `/research/[[...id]]` | 3-column shell (sidebar history · chat · inspector), ToolCallTree, Process, Compaction, Answer + CitationChip, ContextMeter, Ctrl+K palette |
| `/trade/[[...runId]]` | SymbolSearch, PriceChart + indicators, RunSetupBar, PipelineGraph, DecisionCard, TradeApprovalPanel, ResumeBanner, inspector (Node · Debate · Scratchpad · Provenance · Trajectory · State) |
| `/portfolio` | PortfolioOverview, SummaryTiles, HoldingsTable (แก้ inline), AddPositionDialog 6 โหมด, PasteHoldingsDialog, ImportCsvDialog, AllocationChart, ExposureBars, RiskPanel, CorrelationHeatmap, ProposalTable, PlansHistory, WeeklyReports, AI Advisor |
| `/backtest` | setup + เงินต้น, CellGrid, equity curve vs buy & hold vs SMA, what-if, trades |
| `/memory` | MemoryProfileCard (สิ่งที่ AI เห็นจริง), MemoryList ตามหมวด + รอยืนยัน + ประวัติ (drawer), AddMemoryForm, แท็บ ReasoningBank (stats, filter, drawer, retire) |
| `/settings` | LLM deep/quick + failover (drag), ลำดับ data provider ต่อ capability, limits, policy, appearance |

Inspector ใช้ได้ทุกหน้า: Scratchpad, Provenance, Context, State (+ Memory ใน research/portfolio)

---

## 9. Configuration

- ไฟล์ `Product/.env` (fallback) และ `backend/.env` อ่านผ่าน `app/settings.py` (Pydantic Settings, key เป็น `SecretStr`)
- Memory (P17): `MEMORY_ENABLED`, `MEMORY_PROFILE_MAX_TOKENS`, `REASONING_BANK_MIN_SOURCES`, `DISCOVERY_MAX_WHYS`, `DISCOVERY_CONFIDENCE` (ดู `.env.example`)
- ค่าที่ผู้ใช้แก้ใน `/settings` เก็บในตาราง `settings` และ apply ทับตอน start (`harness/config/overrides.py`) API **ไม่ส่ง key กลับ**
- ห้าม commit `.env` / API key และ key ต้องไม่โผล่ใน log หรือ provenance

---

## 10. Dev / Run

| งาน | คำสั่ง (ใน `Product/backend/`) |
|-----|-------------------------------|
| ติดตั้ง | `uv sync` |
| dev server (รัน migration อัตโนมัติ) | `uv run python -m uvicorn app.api.main:app --reload --port 8000` |
| ทดสอบ | `uv run python -m pytest` (live: `-m live`) |
| lint / typecheck | `uv run ruff check app tests alembic` · `uv run python -m mypy app` |
| import contracts | `uv run lint-imports` |
| รันทั้งระบบ (จาก `Product/`) | `npm run dev` (backend :8000 + frontend :3000) |
| export OpenAPI → frontend types | `uv run python -m app.api.export_openapi` แล้ว `pnpm gen:api` |

---

## 11. สถานะและข้อจำกัดที่ทราบ

- Phase 00–13, 15, 16 เสร็จ · **Phase 14 (hardening: evals, Playwright E2E, budget, docs) ยังไม่เริ่ม**
- `claude_code` provider ยังไม่มี streaming และใช้ส่วนตัวเท่านั้น
- Trading: ยังไม่ทำ settle_decisions / reflection (optional §10.4)
- Reference ภายนอก (`../dexter`, `../TradingAgents`, `../FinRobot`, `../deepseek-harness`) เป็น read-only ห้าม import ข้ามโฟลเดอร์
