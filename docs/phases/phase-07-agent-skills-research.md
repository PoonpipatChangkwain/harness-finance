# Phase 07 — Agent loop + Skills + Research workflow & API (D1–D5, D4, F5, F3)

## เป้าหมาย
ประกอบทุกชิ้นให้เป็น **Research Chat backend** ที่ใช้งานได้จริง:
- agent loop แบบ Dexter (iterative tool calling + microcompact + context manager + guards + final answer ในการเรียกแยก)
- ชุด domain tools ครบ
- Skills (SKILL.md) ที่ใช้เป็น scenario
- expert roles
- workflow `research` (retrieve → analyze → verify → compute → synthesize → report)
- API ของ sessions/messages

## ต้องเสร็จก่อน
P03, P05, P06

## อ่านก่อนเริ่ม
- `../dexter/src/agent/agent.ts` (**ทั้งไฟล์**: run loop, streaming, events, final answer, graceful max-iteration)
- `../dexter/src/agent/prompts.ts` (`buildSystemPrompt`: วันที่, tool descriptions, skills metadata, กฎการตอบ)
- `../dexter/src/tools/descriptions/` (ถ้ามี) และ `../dexter/src/tools/registry.ts` (rich descriptions ใน system prompt)
- `../dexter/src/skills/` ทั้งโฟลเดอร์ (loader.ts, registry.ts, types.ts, `dcf/SKILL.md`, `dcf/sector-wacc.md`, `write-memo/*`, `x-research/SKILL.md`)
- `../dexter/src/tools/skill.ts` (SKILL_TOOL_DESCRIPTION, ห้ามเรียกซ้ำ)
- `../dexter/src/tools/subagent/spawn-subagent.ts` (แนวคิด subcall)
- `../FinRobot/finrobot/agents/agent_library.py`, `prompts.py` (role library)
- `../FinRobot/finrobot_equity/core/src/modules/equity_agents/*.py` (บทบาท expert: company overview, competitor, valuation, risks, news summary, major takeaways)
- `../TradingAgents/tradingagents/agents/analysts/*.py` (prompt ของ analyst แต่ละด้าน)

## Tasks

### 7.1 Domain tools ครบชุด (`app/domain/tools/`)
ทุกตัวเป็น `ToolSpec` (contracts §6) ที่ห่อ `DataRouter` หรือ compute โดยมี description แบบ rich (บอกว่าเมื่อไหร่ควรใช้ และเมื่อไหร่ไม่ควรใช้ ตามแบบ Dexter) และคืน `summary` บรรทัดเดียวที่อ่านง่าย

| Tool | category | read_only | ห่ออะไร |
|------|----------|-----------|---------|
| `search_symbols` | data | ✓ | SYMBOL_SEARCH |
| `get_prices` | data | ✓ | PRICES (args: symbol, range/start/end, interval) |
| `get_quote` | data | ✓ | QUOTE |
| `get_market_snapshot` | compute | ✓ | PRICES → `verified_snapshot` (ground truth ของตัวเลขราคา/indicator) |
| `compute_indicators` | compute | ✓ | indicators ที่เลือก |
| `get_company_profile` | data | ✓ | PROFILE |
| `get_fundamentals` | data | ✓ | FUNDAMENTALS |
| `get_statements` | data | ✓ | STATEMENTS (type, period, limit, as_of) |
| `get_news` | data | ✓ | NEWS (fanout) |
| `get_global_news` | data | ✓ | GLOBAL_NEWS |
| `get_social_sentiment` | data | ✓ | SOCIAL (fanout + jev) + สรุป stance count ที่ compute แล้ว |
| `get_macro` | data | ✓ | MACRO (series preset หรือ id) |
| `get_prediction_markets` | data | ✓ | PREDICTION_MARKETS |
| `get_insider_activity` | data | ✓ | INSIDER |
| `get_institutional_holdings` | data | ✓ | INSTITUTIONAL |
| `get_analyst_estimates` | data | ✓ | ANALYST |
| `read_filing` | data | ✓ | FILINGS (section) |
| `web_search` | search | ✓ | WEB_SEARCH |
| `web_fetch` | search | ✓ | WEB_FETCH |
| `compute_returns_risk` | compute | ✓ | returns.py (vol, mdd, beta vs benchmark อัตโนมัติ: `.BK` → `^SET.BK`, US → `SPY`) |
| `compute_dcf` | compute | ✓ | valuation.dcf + sensitivity (args = `DCFAssumptions` ที่ LLM เสนอ, record kind=`llm_assumption`) |
| `compute_comps` | compute | ✓ | peer_multiple_stats + ev_ebitda_value |
| `memory_get` / `memory_search` | memory | ✓ | MemoryStore |
| `memory_update` | memory | ✗ | MemoryStore (ต้องใช้ approval ถ้า namespace = `portfolio`) |
| `skill` | skill | ✓ | โหลด SKILL.md (7.3) |

- tool ที่ต้องการ capability ที่ไม่มี provider configured ต้อง **ไม่ถูกส่งให้ LLM** (registry filter)
- ห้ามมี tool ให้ LLM "คำนวณเอง" ตัวเลขทุกตัวต้องมาจาก compute tools (F1)

### 7.2 Agent loop (`app/harness/runtime/agent_loop.py`)
port `Agent.run` ของ Dexter เป็น async generator ที่ emit ผ่าน EventBus:

```
init scratchpad(query) → build system prompt
while iteration < max_iterations:
    limit = guard.check(ctx);  if limit: break → graceful
    messages = context_manager.before_llm_call(ctx, messages)     # P06
    resp = llm.complete(messages, tier="deep", tools=node_tools, purpose="agent")  # stream deltas → llm.delta(reasoning)
    update ctx.last_api_input_tokens, usage; emit llm.finished, context.usage
    if no tool_calls: break                                        # agent พร้อมตอบ
    results = executor.run_calls(resp.tool_calls, ctx)             # P05
    append assistant(tool_calls) + tool messages (content = ToolResult.content)
    inject tool_usage_status reminder ถ้ามี tool ใกล้ limit (repeat-tool-reminder ของ dsh)
    iteration += 1
final = final_answer(ctx)                                          # 7.4
```

- **graceful stop:** เมื่อชน limit, cancel (สำหรับ partial) หรือ max_iterations ให้ไปทำ final answer ต่อ พร้อมใส่ข้อความใน prompt ว่า "หยุดเพราะ … ให้ตอบจากข้อมูลที่มีและระบุว่าขาดอะไร" (ตามแบบ Dexter: "Reached maximum iterations…")
- คำตอบ text ระหว่าง loop ถือเป็น reasoning (`llm.delta{channel:"reasoning"}`) ส่วน answer จริงมาจาก final call
- ทุก tool call ต้องผ่าน executor ห้ามเรียก fn ตรงๆ

### 7.3 Skills (`app/harness/skills/` + `app/domain/skills/`)
- loader: สแกน `app/domain/skills/**/SKILL.md` แล้ว parse YAML frontmatter (`name`, `description`, `category`, `inputs?`, `tools?`) ถ้า frontmatter ผิดให้ log warning แล้วข้าม (port loader.ts และ registry.ts)
- system prompt มีรายการ skill (name + description) ตามแบบ Dexter
- `skill` tool คืน body ของ SKILL.md และแปลง link ภายใน (เช่น `sector-wacc.md`) เป็นเนื้อหาแนบท้าย และแต่ละ skill เรียกได้ **ครั้งเดียวต่อ query** (บังคับใน executor ด้วย `max_calls_per_run=1` แยกตาม skill name)
- ถ้าผู้ใช้เลือก scenario ใน UI (`skill` param) ให้ inject skill body ตั้งแต่ต้น (ไม่ต้องให้ LLM เรียก) และ emit tool row แบบ synthetic `skill` เพื่อให้ UI เห็น
- **Skills ที่ต้องเขียน** (ภาษาอังกฤษใน body แต่ให้ตอบตามภาษาผู้ใช้; ทุก skill ต้องบอกว่า tool ไหนใช้คำนวณ):

| category | skill | สรุปขั้นตอน |
|----------|-------|-------------|
| research | `earnings-review` | statements ล่าสุดเทียบปีก่อน/ไตรมาสก่อน → compute growth/margins → news รอบประกาศงบ → analyst estimates → สรุป beat/miss + ประเด็น |
| research | `macro-outlook` | FRED presets → prediction markets → global news → ผลต่อ sector/ตลาดไทย (ดอกเบี้ย, USD/THB) |
| research | `sector-scan` | symbols ใน sector (static CSV) → snapshot + returns ของแต่ละตัว → ranking ที่ compute แล้ว → ข่าวเด่น |
| research | `news-digest` | news fanout + global news → จัดกลุ่มตามธีม → ผลกระทบ (อิง news_integrator.py: category/importance) |
| analysis | `company-valuation` | port `dcf/SKILL.md` + `sector-wacc.md` ทั้งหมด แต่ **ขั้นคำนวณเปลี่ยนเป็นเรียก `compute_dcf`/`compute_comps`** แล้วตามด้วย sensitivity |
| analysis | `technical-analysis` | snapshot + indicators + returns/risk → แนวรับ/ต้าน (compute จาก swing high/low) → สรุปสัญญาณ |
| analysis | `sentiment-check` | social fanout + jev + news sentiment → stance counts (compute) → ความเห็น |
| data_processing | `compare-companies` | ดึง fundamentals ของหลายบริษัท → ตารางเทียบ (compute) |
| reporting | `investment-memo` | port `write-memo/` (memo-style.md, memo-template.html → markdown) และต้องใช้ข้อมูลจาก scratchpad เท่านั้น |
| reporting | `x-research` | port `x-research/SKILL.md` (ใช้เมื่อมี X key เท่านั้น ใส่ `requires: [x_api]` ใน frontmatter) |

### 7.4 Final answer (`app/harness/runtime/final_answer.py`)
- LLM call แยก tier deep ที่ **ไม่ bind tools** (Dexter) โดย input = system (กฎการตอบ) + query + `scratchpad.build_final_context()` (ผลเต็ม ไม่ถูก compact; ถ้าเกิน window ให้ใช้ compaction summary + ผลล่าสุด)
- กฎใน prompt:
  - ตัวเลขทุกตัวต้องมาจากข้อมูลที่ให้ และแนบ `[^prov_id]`
  - ห้ามคำนวณตัวเลขใหม่เอง ถ้าต้องใช้ตัวเลขที่ยังไม่มีให้บอกว่าไม่มี
  - ระบุข้อมูลที่ unavailable
  - ตอบภาษาเดียวกับผู้ใช้
  - ต้องมี disclaimer สั้น (ไม่ใช่คำแนะนำการลงทุน)
- stream เป็น `llm.delta{channel:"answer"}` แล้วตรวจด้วย `citations.py` + `number_check.py` จากนั้น emit `answer.final` และบันทึก message ลง `messages`

### 7.5 Expert roles (`app/domain/agents/`)
- `library.py` (ตามแบบ `agent_library.py`): dict ของ role → `{title, profile (system prompt), tools, tier}` โดย role ได้แก่ `research_lead` (default), `equity_analyst`, `macro_strategist`, `technical_analyst`, `sentiment_analyst`, `valuation_specialist`, `risk_officer`
- prompt เก็บเป็นไฟล์ `app/domain/agents/prompts/<role>.v1.md` และบันทึก version ลง provenance/`runs.meta`
- workflow `research` ใช้ `research_lead` เป็น loop หลัก ส่วน skill ระบุ role ที่ควรใช้ใน frontmatter (`role: valuation_specialist`) เพื่อเปลี่ยน system prompt

### 7.6 Research workflow (`app/domain/workflows/research.py`) — F5
ประกาศเป็น `WorkflowSpec` ตาม contracts §9 (ยังไม่ใช้ LangGraph ใน phase นี้) โดยใช้ `SequentialRunner` แบบง่ายใน `app/harness/workflow/sequential.py` ที่รัน node ตามลำดับ, emit `node.started/finished` และ `validate` output (P09 จะย้ายไปใช้ graph engine)
1. `plan` (quick): จัดประเภทคำถามแล้วเลือก skill (ถ้าผู้ใช้ไม่ได้เลือก) และ role
2. `retrieve_analyze` (deep): agent loop 7.2
3. `verify`: number_check + ตรวจ citations และถ้าพบตัวเลขที่ verify ไม่ได้เกิน 3 ตัว ให้วน agent loop อีก 1 รอบพร้อมบอกให้ยืนยันตัวเลขเหล่านั้นด้วย tool (จำกัด 1 รอบ)
4. `synthesize_report`: final answer 7.4

### 7.7 API (`app/api/routes/research.py`, `sessions.py`, `skills.py`)
- `GET /api/skills` → `[{name, description, category, role, available: bool, missing: [provider_ids]}]`
- `POST /api/sessions`, `GET /api/sessions?kind=research`, `GET /api/sessions/{id}` (messages + runs พร้อม usage)
- `POST /api/sessions/{id}/messages {text, skill?}`:
  - บันทึก user message แล้วสร้าง run (workflow=research) → `{run_id}`
  - conversation ต่อเนื่อง: ส่ง messages ก่อนหน้าของ session (user + answer) เข้า loop เป็น history
- ตั้งชื่อ session อัตโนมัติจากคำถามแรก (quick LLM, ≤ 6 คำ)

### 7.8 Tests
- agent loop + FakeLLM: tool call → result → final และลำดับ event ถูกต้อง
- max_iterations = 2 → graceful final + `guard.limit`
- tool error → LLM ได้เห็น error แล้วเรียก tool อื่น
- similar query ครั้งที่ 4 → limited
- skill ถูกเรียกซ้ำ → limited
- ผู้ใช้เลือก scenario → skill body อยู่ใน system/context
- final answer ที่มีตัวเลขไม่มี provenance → `unverified_numbers` ไม่ว่าง และ verify loop ทำงาน 1 ครั้ง
- tool ที่ provider ไม่ configured ไม่อยู่ใน tools ที่ส่ง LLM
- API: สร้าง session → ส่ง message → อ่าน SSE จนจบ (FakeLLM ผ่าน dependency override)
- live (`-m live`): "PTT ราคาปัจจุบันและ RSI เท่าไร" ต้องจบภายใน limits และมี citation

## Definition of Done
- [ ] `POST /api/sessions/{id}/messages {"text":"สรุปงบล่าสุดของ AAPL","skill":"earnings-review"}` ได้คำตอบพร้อม citations ผ่าน SSE
- [ ] event stream แสดงครบ: node → llm → tool (concurrent) → context.usage → answer.final
- [ ] ใส่แค่ LLM key (ไม่มี data key) แล้วยังตอบได้ด้วย Tier A
- [ ] ไม่มีตัวเลขใน answer ที่ไม่มี citation ในกรณีทดสอบหลัก 5 เคส (ใช้ live eval ที่รันด้วยมือแล้วรายงานผล)

## นอกขอบเขต
UI (P08) และ LangGraph (P09)
