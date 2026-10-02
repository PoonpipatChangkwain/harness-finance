# Phase 09 — Graph engine: LangGraph adapter, state/reducers, checkpoint/resume, approval gate, policy (T1–T4)

## เป้าหมาย
เปลี่ยน `WorkflowSpec` (plain data ใน domain) ให้รันบน LangGraph โดย:
- **domain ไม่รู้จัก LangGraph** (F2)
- node คืน partial update เท่านั้นและเขียนได้เฉพาะ field ที่ประกาศ (T2)
- router ครอบคลุมทุก path (T2)
- checkpoint หลังทุก node และ resume จาก node ที่ล้มได้ (T3)
- approval ผ่าน `interrupt()` และ policy gate ที่คำนวณแบบ deterministic (T4)

## ต้องเสร็จก่อน
P05 (P07 และ P08 ควรเสร็จแล้ว เพื่อย้าย research workflow มาทดสอบ)

## อ่านก่อนเริ่ม
- `docs/contracts.md` §8, §9, §10 (node/approval/checkpoint events), §11 (resume, approvals)
- `../TradingAgents/tradingagents/graph/setup.py` (**ทั้งไฟล์**: StateGraph, ToolNode, `_tools_or_clear`, DEBATE_PATH_MAP, RISK_ANALYSIS_PATH_MAP)
- `../TradingAgents/tradingagents/graph/trading_graph.py` (propagate, checkpoint_scope, signature, recompile with checkpointer, clear on success)
- `../TradingAgents/tradingagents/graph/checkpointer.py` (**ทั้งไฟล์**)
- `../TradingAgents/tradingagents/graph/propagation.py` (initial state, recursion_limit, stream_mode)
- `../TradingAgents/tradingagents/graph/conditional_logic.py`, `analyst_execution.py`
- `../TradingAgents/tradingagents/agents/state.py`
- `../deepseek-harness/packages/workflow/workflow/src/` (invariant.ts, types.ts), `packages/session/` (durable session แนวคิด)
- เอกสาร LangGraph: `interrupt`, `Command(resume=...)`, `SqliteSaver`/`AsyncSqliteSaver`, reducers ด้วย `Annotated[..., operator.add]`

## Tasks

### 9.1 Dependencies
`langgraph`, `langgraph-checkpoint-sqlite` (import ได้เฉพาะใน `app/harness/graph/`)

### 9.2 Spec validation (`app/harness/workflow/validate.py`)
`validate_spec(spec)` ตรวจตอน register (fail fast):
- node id ไม่ซ้ำ และ entry มีอยู่จริง
- ทุก edge/router ชี้ node ที่มีอยู่หรือ `END`
- ทุก node reachable จาก entry
- ทุก state field ที่มีหลาย writer ต้องมี reducer ที่ไม่ใช่ `replace`
- `path_map` ของ router ต้องมี key ครบทุกค่าที่ router คืนได้: ให้ router ประกาศ `returns: frozenset[str]` แล้วเช็กว่า ⊆ path_map (ทำแบบ `DEBATE_PATH_MAP`) และมี `"__fallback__"` เสมอ

### 9.3 LangGraph adapter (`app/harness/graph/compile.py`)
`compile_workflow(spec, *, checkpointer, node_context_factory) -> CompiledWorkflow`
- สร้าง `StateGraph(spec.state_model)` โดยแปลง `reducers` เป็น `Annotated` (append → `operator.add` บน list, merge → dict merge, add → ตัวเลข)
- ห่อทุก node ด้วย `wrap_node(node_spec)`:
  1. emit `node.started`
  2. สร้าง `NodeContext` (llm, tools executor ที่จำกัดเฉพาะ `node_spec.tools`, emit ที่ผูก `node`, request_approval)
  3. เรียก `fn(state_snapshot, ctx)`
  4. ตรวจว่า key ที่คืนเป็นส่วนหนึ่งของ `writes` ถ้าไม่ใช่ให้ raise `StateWriteViolation` (ตรวจทั้ง dev และ prod)
  5. emit `node.finished{duration, summary}` และ `checkpoint.saved`
  6. exception → emit `node.finished{status:"error"}` แล้ว raise ต่อ (ให้ run fail โดย checkpoint ยังอยู่)
- router: ห่อ `fn` ถ้าคืนค่านอก path_map ให้ใช้ `__fallback__` และ emit `warning{code:"router_fallback"}`
- `recursion_limit` จาก settings (default 100 ตาม `max_recur_limit`)
- stream ด้วย `stream_mode="updates"` เพื่อรู้ว่า node ไหนเพิ่งเสร็จ

### 9.4 Checkpoint (`app/harness/graph/checkpoint.py`) — T3
port `checkpointer.py`:
- `db_path(data_dir, scope)`: `checkpoints/{safe(scope)}.db` โดย scope = symbol (trade) หรือ `_workflow_{id}`
- `thread_id(scope, date, signature)` = sha256(`SCOPE:DATE:SIG`)[:16]
- `graph_signature(spec, input)` = hash ของ `spec.id`, `spec.version` และค่าของ `signature_fields` (เช่น analysts ที่เลือก, debate rounds, มี portfolio หรือไม่) เพื่อไม่ให้ resume ข้าม graph ที่รูปร่างต่างกัน
- `get_checkpointer` (Async SqliteSaver), `checkpoint_step`, `clear_checkpoint` (ลบ rows ใน `writes` + `checkpoints` ของ thread) และ `clear_all` ที่ลบ `-wal`/`-shm` ด้วย
- ถ้า run สำเร็จให้ clear checkpoint (ตั้งค่า `keep_checkpoints_on_success=false` ได้) และเก็บ `thread_id` ใน `runs.thread_id`

### 9.5 Workflow runner (`app/harness/workflow/runner.py`)
แทน `SequentialRunner` ของ P07:
- `start(spec, input, *, scope, date)`: สร้าง run → compile → `astream(initial_state, config={"configurable":{"thread_id":tid}})` → map updates เป็น events → เมื่อเจอ `__interrupt__` ให้บันทึก approval แล้วตั้ง `runs.status="waiting_approval"` และหยุด task (ไม่ block thread)
- `resume(run_id)`: ใช้ thread_id เดิมแล้ว `astream(None, config)` เพื่อให้ LangGraph ทำต่อจาก checkpoint ล่าสุด ถ้า signature ไม่ตรงให้ปฏิเสธพร้อมเหตุผล
  - node ที่ `idempotent=False` และเคยเริ่มแล้วแต่ไม่จบ ต้องไม่ถูกรันซ้ำแบบเงียบ ให้ส่ง approval ขอยืนยันก่อน
- `answer_approval(approval_id, response)`: บันทึก, emit `approval.resolved` แล้ว `astream(Command(resume=response), config)`
- server restart: run ที่ `running` ให้ mark `interrupted` (มีจาก P05) ส่วน UI เรียก resume ได้
- **ย้าย research workflow มาใช้ runner นี้** (scope = `_workflow_research`) แล้วตรวจว่า event เหมือนเดิม

### 9.6 Approval + Policy gate — T4
- `domain/models/approval.py`: `ApprovalRequest(kind, title, proposal: dict, policy_results: list[PolicyResult], provenance_ids)` และ `ApprovalResponse(outcome: approved|rejected|edited, edits?: dict, comment?)`
- `NodeContext.request_approval(req)` → ใน adapter เรียก `interrupt(req.model_dump())` แล้วคืน `ApprovalResponse` ตอน resume
- `domain/policy/`:
  - `PolicyRule` Protocol: `id`, `description`, `evaluate(proposal, context) -> PolicyResult(rule_id, passed, severity: "block"|"warn", message, values)` เป็น deterministic ล้วนและค่าที่ใช้ต้องมาจาก compute
  - `PolicySet.evaluate(...)` → รวมผล ถ้ามี `block` ที่ไม่ผ่านให้ผลเป็น `blocked`
  - rules ชุดแรก (ใช้ใน P10/P12): `MaxPositionWeight(0.20)`, `MaxSectorWeight(0.40)`, `RequireStopLoss`, `MaxRiskPerTrade(0.02 of equity)`, `CashSufficient`, `ValidSymbol`, `MinConfidence(0.6)`, `NoShortIfDisallowed` โดยค่าตั้งได้จาก settings
- **Gate pattern มาตรฐาน** (ใช้ซ้ำทุก workflow): `proposal node (structured, T5) → policy node (compute) → router(route_decision + policy) → approval node (interrupt) → execute node (บันทึก plan เท่านั้น)`
  - policy blocked → ส่งกลับ proposal node พร้อมเหตุผล (จำกัด 1 รอบ) ถ้ายังไม่ผ่านให้ REJECT
  - REVIEW หรือ APPROVE → approval (มนุษย์เป็นคนตัดสินเสมอใน MVP)
  - edited → re-run policy กับค่าที่แก้ แล้ว execute ถ้าผ่าน

### 9.7 API
- `GET /api/workflows/{id}/graph` → `{nodes:[{id,label,kind,phase}], edges:[{source,target,conditional,label?}]}` จาก `spec.graph_view()`
- `POST /api/runs/{id}/resume` → `{run_id, resumed_from_node}`
- `POST /api/approvals/{approval_id}` body `ApprovalResponse`
- `GET /api/runs/{id}` เพิ่ม `thread_id`, `current_node`, `pending_approval` และ `resumable: bool`
- `GET /api/runs/{id}/state` → graph state ล่าสุดจาก checkpoint (สำหรับ tab State)

### 9.8 Frontend (เล็ก)
- เปิดใช้ `ApprovalPanel` ใน Conversation (research ใช้กับ `memory_update` namespace portfolio)
- ปุ่ม "Resume" ใน header ของ run ที่ `resumable`

### 9.9 Tests (ใช้ workflow ทดสอบใน `tests/harness/graph/fixtures.py` ที่มี node A→B→C + router + approval)
- validate_spec: node ซ้ำ, unreachable, router path ไม่ครบ, multi-writer ไม่มี reducer → error
- write violation → error
- router คืนค่าแปลก → fallback + warning
- **crash & resume:** C raise ครั้งแรก → run failed แล้ว resume → A และ B **ไม่ถูกเรียกซ้ำ** (นับจำนวนครั้ง) และ C สำเร็จ
- signature ต่างแล้ว resume → ถูกปฏิเสธ
- interrupt → status waiting_approval → approve → execute ทำงาน / reject → END / edited → policy รันใหม่
- policy block → วนกลับ 1 ครั้ง → REJECT
- clear checkpoint on success และลบ wal/shm
- research workflow บน runner ใหม่ให้ event sequence ตรงกับ P07 (snapshot)

## Definition of Done
- [ ] `lint-imports`: `langgraph` อยู่แค่ใน `app/harness/graph`
- [ ] demo: `POST /api/dev/graph-demo` → ฆ่า process ระหว่าง node B → เปิดใหม่ → `POST /runs/{id}/resume` → จบโดย A ไม่รันซ้ำ
- [ ] approval ผ่าน API ใช้ได้ครบ 3 outcome
- [ ] test ผ่าน และ coverage ของ `app/harness/graph` + `app/harness/workflow` ≥ 90%

## นอกขอบเขต
agent ของ trading (P10) และ UI graph (P11)
