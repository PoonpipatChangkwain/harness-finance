# Phase 05 — Harness runtime: events, storage, scratchpad, provenance, tool executor, guards (D2, D3, D5, F3)

## เป้าหมาย
สร้าง "เครื่องยนต์" ที่ทุก workflow ใช้ร่วมกัน:
1. **Run manager** เริ่ม run เป็น background task และ cancel ได้
2. **Event bus** บันทึกทุก event ลง DB แล้ว stream ผ่าน SSE และ replay ได้
3. **State 3 ชนิด (D2):** conversation, scratchpad (JSONL) และ memory
4. **Provenance store** พร้อม lineage
5. **Tool executor (D3):** validate → permission → execute → normalize
6. **Guards (D5):** iterations, loop detection, timeout, budget และ retry

## ต้องเสร็จก่อน
P02, P04

## อ่านก่อนเริ่ม
- `docs/contracts.md` §6 (Tool), §10 (Events), §11 (API: runs/*), §12 (Storage)
- `../dexter/src/agent/scratchpad.ts` (**อ่านทั้งไฟล์**: JSONL, ToolLimitConfig, canCallTool, recordToolCall, Jaccard similarity, getToolUsageStatus, cleared/active tool results)
- `../dexter/src/agent/run-context.ts`, `types.ts` (event types)
- `../dexter/src/agent/tool-executor.ts` (**อ่านทั้งไฟล์**: batching concurrent, approval, denied, limit events)
- `../dexter/src/tools/registry.ts`, `types.ts` (`formatToolResult`)
- `../dexter/src/permissions/engine.ts`, `types.ts`, `rules.ts` (allow/ask/deny + session approval)
- `../dexter/src/utils/concurrency.ts`, `progress-channel.ts`
- `../deepseek-harness/packages/guard/timeout-policy/src/index.ts`, `repeat-tool-reminder/src/index.ts`
- `../deepseek-harness/packages/spill/` (ดูวิธีจัดการ output ใหญ่)
- `../TradingAgents/tradingagents/decision_log.py` (append-only log)

## Tasks

### 5.1 Storage (`app/harness/storage/`)
SQLAlchemy models + Alembic migration:
- `sessions(id, kind, title, created_at, updated_at, meta json)`
- `messages(id, session_id, run_id, role, content, created_at, meta json)` สำหรับ conversation state ที่แสดงใน UI
- `runs(id, session_id?, workflow, status, input json, output json, error, thread_id?, started_at, finished_at, usage json)` โดย `usage` = {input_tokens, output_tokens, cost_usd, llm_calls, tool_calls}
- `events(run_id, seq, ts, type, node, call_id, parent_call_id, payload json)` PK(run_id, seq)
- `provenance(id, run_id, kind, value json, unit, source, tool, calculation, params json, inputs json, agent, as_of, fetched_at, url)` + index run_id
- `approvals(id, run_id, node, kind, status, request json, response json, created_at, resolved_at)`
- `memory(id, namespace, key, value json, updated_at)` เป็น persistent memory แบบ key-value ตาม namespace เช่น `user`, `portfolio`, `decisions`
- repository functions แบบ async แยกไฟล์ต่อ aggregate

### 5.2 Events (`app/harness/events/`)
- `schema.py`: Pydantic model ของทุก event type ตาม contracts §10 (discriminated union ด้วย `type`) และ export JSON schema ไปที่ `backend/schemas/events.schema.json` ด้วยคำสั่ง `uv run python -m app.harness.events.export` เพื่อให้ frontend test เทียบได้
- `bus.py`: `EventBus`
  - `emit(run_id, type, payload, *, node=None, call_id=None, parent_call_id=None)` → กำหนด seq แบบ monotonic ต่อ run (ใช้ `asyncio.Lock` ต่อ run), บันทึก DB แล้ว publish ไปยัง subscriber ใน memory (`asyncio.Queue` ต่อ subscriber)
  - `subscribe(run_id, after_seq)` → async iterator ที่ replay จาก DB ก่อน (seq > after_seq) แล้วค่อยต่อ live โดยห้ามให้ event หายหรือซ้ำในช่วงรอยต่อ (subscribe queue ก่อน query DB แล้ว dedupe ด้วย seq)
  - ปิด stream เมื่อได้ `run.finished`
- `EventPort` implementation ที่ domain ใช้ (`domain/ports.py` เพิ่ม `EventPort` Protocol)
- `llm.delta` ไม่ต้องเก็บทุก chunk ใน DB: รวม (coalesce) เป็นก้อนละ ~200ms แล้วค่อยบันทึก แต่ส่ง live ได้ทุก chunk

### 5.3 Run manager (`app/harness/runtime/runs.py`)
- `RunManager.start(workflow_id, input, session_id?) -> run_id`: สร้าง row, emit `run.started` และ `asyncio.create_task(...)` โดยเก็บ task + `CancelToken`
- `cancel(run_id)`: ตั้ง token แล้ว cancel task และ emit `run.finished{status:"cancelled"}` ส่วน executor/LLM ต้องเช็ก token ทุกจุดที่รอ
- ถ้า task มี exception ที่ไม่ได้จับ ให้ emit `run.finished{status:"failed", error}` เสมอ (ห้ามเงียบ)
- ตอนเริ่ม server ให้ mark run ที่ status=running ค้างอยู่เป็น `interrupted` (resume ได้ใน P09)
- `RunContext` (port run-context.ts): `run_id`, `query`, `scratchpad`, `token_counter`, `started_at`, `iteration`, `last_api_input_tokens`, `usage`, `cancel`, `limits`

### 5.4 Scratchpad (`app/harness/state/scratchpad.py`) — D2
port `scratchpad.ts`:
- ไฟล์ `DATA_DIR/scratchpad/{run_id}.jsonl` แบบ append-only (flush ทุกครั้ง)
- entry types: `init`, `tool_result`, `thinking`, `compaction`, `guard`, `final`, `note`
- `tool_result` เก็บ `call_id`, `tool`, `args`, `result` (**เต็ม ไม่ตัด**), `provenance_ids`, `duration_ms`
- tool limit ตาม `ToolLimitConfig(max_calls_per_tool=3, similarity_threshold=0.7)`:
  - `can_call_tool(tool, query) -> (allowed, warning)` ใช้ Jaccard ของชุดคำที่ normalize แล้ว (lowercase, ตัด stopwords/ตัวเลขวันที่) ตามแบบ scratchpad.ts
  - `record_tool_call(tool, query)`
  - `tool_usage_status()` สำหรับใส่ใน prompt ("คุณเรียก get_prices ไปแล้ว 2/3 ครั้ง")
- tracking การ clear/compact: `mark_cleared(call_ids)`, `active_tool_result_count()` (ใช้ใน P06)
- `build_final_context()` คืนผลลัพธ์ tool ทั้งหมด (ไม่ถูก compact) สำหรับ final answer call
- **Conversation state** (`state/conversation.py`): list ของ `LLMMessage` ที่ส่งให้ LLM ซึ่ง compact ได้ และแยกจาก scratchpad ชัดเจน
- **Memory** (`state/memory.py`): `MemoryStore.get/put/search(namespace, key|prefix)` ที่ agent ใช้ผ่าน tool `memory_get` และ `memory_update` (P07) เท่านั้น

### 5.5 Provenance store (`app/harness/provenance/`)
- `store.py`: `ProvenanceStore.add_many(records, run_id)` (upsert by id), `get(id)`, `list(run_id)` และ `lineage(id, depth=5)` ที่คืน tree ของ inputs
- `number_check.py`: `find_unverified_numbers(markdown, records) -> list[str]`
  - extract ตัวเลขจากข้อความ (รองรับ `1,234.5`, `12.3%`, `$45`, `฿45`, `1.2B`, `3.4 ล้าน`)
  - normalize แล้วจับคู่กับ value ใน records ของ run ด้วย tolerance (relative 0.5% หรือต่างกันแค่การปัดทศนิยม) และข้ามปี/วันที่/ลำดับข้อ
  - ใช้กับ `answer.final.unverified_numbers`
- `citations.py`: ให้ LLM ใส่ marker `[^prov_xxx]` หลังตัวเลขใน final answer แล้ว parse เป็น `citations` จากนั้นแปลง marker เป็น `[n]` ก่อนส่ง UI

### 5.6 Tool registry + executor (`app/harness/executor/`) — D3
- `registry.py`: `ToolRegistry.register(spec: ToolSpec)`, `get(name)` และ `for_node(names)` โดยเปิดใช้ tool เฉพาะเมื่อ data capability ที่ tool นั้นพึ่งมี provider configured (ตามแบบ Dexter ที่ตรวจ env)
- `permissions.py`: `PermissionPolicy.evaluate(spec, args, run) -> "allow" | "ask" | "deny"` โดย default คือ read_only → allow และ `requires_approval` → ask ส่วน ask จะใช้ `request_approval` (P09 จะทำให้ครบ ตอนนี้ให้ raise `NotImplementedError` ถ้าเจอ) และจำ `session_approved_tools` ตามแบบ Dexter
- `executor.py`: `ToolExecutor.run_calls(calls: list[ToolCallReq], ctx) -> list[ToolResult]` port `AgentToolExecutor`:
  1. แบ่ง batch: tool read_only ที่ติดกันรวมเป็น batch concurrent (สูงสุด `max_concurrency=10` ด้วย semaphore) ส่วน tool ที่ไม่ read_only รันเดี่ยว **ตามลำดับเดิม**
  2. ต่อ call: emit `tool.started` → **validate** args (`args_model.model_validate`; ถ้าผิดได้ `status="error"`, type `ValidationError` พร้อมบอก field ที่ผิดให้ LLM แก้) → **guard** (`can_call_tool`; ไม่ผ่านได้ `status="limited"` + emit `guard.limit`) → **permission** (deny → `denied`) → **execute** ด้วย `asyncio.wait_for(timeout_s)` + cancel token → **normalize** เป็น `ToolResult`
  3. normalize: `content` = serialize data เป็น text/markdown table (port แนวคิดจาก `formatters.ts` และ `stringify_output` ของ FinRobot) แล้วตัดที่ `max_chars` (default 12k; ถ้าเกินตั้ง `truncated=True` และต่อท้ายว่า "…[truncated; full result in scratchpad call_id=…]")
  4. บันทึก scratchpad (ผลเต็ม) + provenance store แล้ว emit `tool.finished` (มี preview ≤ 2k)
  5. exception ทุกชนิด → `ToolResult(ok=False, status="error", error=...)` **ห้าม raise ออกจาก executor** ยกเว้น `CancelledError`
  - รองรับ subcall: tool ที่เป็น skill/agent เรียก tool อื่นต่อได้โดยส่ง `parent_call_id`

### 5.7 Guards (`app/harness/runtime/limits.py`) — D5
- `RunLimits(max_iterations=10, max_tool_calls_total=40, max_calls_per_tool=3, similarity_threshold=0.7, tool_timeout_s=30, llm_timeout_s=120, run_wall_clock_s=600, max_cost_usd=1.0, max_output_tokens_total=200k)` override ได้จาก settings และต่อ request
- `LimitGuard.check(ctx) -> LimitHit | None` ถูกเรียกก่อนทุก LLM call และทุก tool batch ถ้าชนให้ emit `guard.limit` แล้วให้ loop ทำ graceful stop (P07)
- `retry.py`: generic `with_retry(fn, classify, attempts=3, base=0.5)` ใช้ร่วมกับ data (P02) และ LLM (P04) ถ้าตอนนั้นเขียนแยกไว้ให้ refactor มาใช้ตัวนี้

### 5.8 API (`app/api/routes/runs.py`)
- `GET /api/runs/{id}`, `POST /api/runs/{id}/cancel`
- `GET /api/runs/{id}/events` เป็น SSE (`sse-starlette`) อ่าน `Last-Event-ID` header หรือ `?after=` โดยทุก event ใช้ `id: seq`, `event: type` และ `data: json` ส่ง heartbeat ทุก 15s (comment ping) และปิดเมื่อจบ run
- `GET /api/runs/{id}/scratchpad?offset=&limit=&tool=`
- `GET /api/runs/{id}/provenance`, `GET /api/provenance/{pid}?lineage=1`
- **Demo workflow** `echo_tools` (ใช้ทดสอบ phase นี้เท่านั้น): เรียก tool `get_prices` + `compute_indicators` แบบ concurrent ผ่าน executor โดยไม่มี LLM แล้วจบ run เพื่อให้เห็น event ครบวงจร และ endpoint `POST /api/dev/echo-run?symbol=PTT` (เฉพาะ `APP_ENV=dev`)

### 5.9 Domain tools ชุดแรก (`app/domain/tools/`)
เพื่อทดสอบ executor ให้สร้าง `get_prices`, `get_quote`, `compute_indicators` (เรียก `verified_snapshot`) และ `search_symbols` ตาม `ToolSpec` (ชุดเต็มอยู่ใน P07)

### 5.10 Tests
- bus: seq ต่อเนื่อง, replay + live ไม่หาย/ไม่ซ้ำ (race test ที่ emit ระหว่าง subscribe), `Last-Event-ID`
- executor: read_only 3 ตัวรันพร้อมกัน (ดู timestamps ซ้อนกัน), non-read-only รันตามลำดับ, validation error ต้องคืนเป็น result, timeout ได้ status=timeout, exception ได้ status=error, cancel หยุดทุกตัว, output ใหญ่ถูกตัดและ scratchpad มีผลเต็ม
- scratchpad: JSONL อ่านกลับได้, Jaccard similarity เป็นไปตาม threshold, limit 3 ครั้ง
- limits: ชน iterations/budget/wall clock แล้วได้ `guard.limit`
- number_check: เคสไทย/อังกฤษ ≥ 20 เคส
- SSE endpoint: ใช้ httpx stream อ่าน echo run จนจบ

## Definition of Done
- [ ] `POST /api/dev/echo-run?symbol=PTT` แล้ว `curl -N /api/runs/{id}/events` เห็น `run.started → tool.started×2 → tool.finished×2 → run.finished`
- [ ] ต่อ SSE ใหม่ด้วย `Last-Event-ID: 2` ต้องได้เฉพาะ seq ≥ 3
- [ ] scratchpad JSONL มีผลเต็มของทั้งสอง tool และ provenance store มี record ของราคาและ indicator โดย indicator มี `inputs` ชี้ไปที่ record ของราคา (lineage ทำงาน)
- [ ] test/lint/mypy/import-linter ผ่าน

## นอกขอบเขต
agent loop ที่มี LLM (P07), compaction (P06) และ approval UI (P09)
