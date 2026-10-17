# Phase 17 — AI Portfolio Advisor ที่จำผู้ใช้ได้และเก่งขึ้นเรื่อย ๆ (Long-term Memory · ReasoningBank · 5 Whys · หน้า Memory)

> branch `FinTechathon` — เปลี่ยนโปรดักต์ให้เป็น **แพลตฟอร์ม AI ให้คำปรึกษาพอร์ตลงทุน** ที่ (1) เข้าใจผู้ใช้คนนั้นจริง ๆ ข้าม session (2) เรียนรู้จากทุก run/ทุกคนแล้วแม่นขึ้น (3) ถามหาเจตนาที่แท้จริงก่อนแนะนำ (4) โปร่งใส: ผู้ใช้เห็น แก้ และลบสิ่งที่ AI จำได้

## เป้าหมาย

| # | ฟีเจอร์ | ผลลัพธ์ที่ต้องเห็น |
|---|---------|-------------------|
| 1 | **Long-term user memory** (port pattern ของ Mem0: extract → reconcile `ADD/UPDATE/SUPERSEDE/RETIRE/NOOP`) | ผู้ใช้บอก "ผมชอบลงทุนเรื่อย ๆ" → ระบบจำ; ต่อมาบอก "ตอนนี้ชอบเร็ว ๆ แล้ว" → ความจำเก่าถูก **supersede** (ไม่ลบทิ้ง — ยังดูประวัติได้) และความจำใหม่ถูกใช้ในคำแนะนำครั้งถัดไปทันที |
| 2 | **ReasoningBank** (Google 2025: memory item = title/description/content จากทั้ง run ที่สำเร็จและล้มเหลว) | ทุก run ที่ตัดสินผลได้ (approve/reject แผน, trade outcome, research ที่ชน limit) กลั่นเป็นบทเรียน **ที่ไม่มีข้อมูลส่วนตัว** ลง bank กลาง; run ใหม่ดึง top-1–2 มาใส่ prompt และ success rate ของ item อัปเดตตามผลจริง |
| 3 | **5 Whys** | ทุกคำขอใน chat ที่สื่อว่าจะเปลี่ยนพอร์ต (ซื้อ/ขาย/all-in/ตั้งเป้าผลตอบแทน) AI **ตอบข้อมูลก่อน** แล้วถาม "ทำไม" แบบสุภาพ ≤ 1 คำถามต่อข้อความ รวม ≤ 5 ต่อเป้าหมาย มีปุ่มข้ามเสมอ; เมื่อรู้ root goal แล้วสรุปกลับให้ยืนยัน → บันทึกเป็น memory |
| 4 | **หน้า `/memory`** | ผู้ใช้เห็นทุกอย่างที่ AI จำ (แยกหมวด, confidence, ที่มา, วันที่) ยืนยัน/แก้/ลบ/ดูประวัติได้ + ดู ReasoningBank ได้ |

**การตัดสินใจที่ล็อกแล้ว (2026-10-17):**
- **single-user ไปก่อน**: ทุกตารางใหม่มีคอลัมน์ `user_id` (ค่า `usr_default`) และ API อ่าน header `X-User-Id` (ไม่มี = `usr_default`) — ไม่มี UI เลือกโปรไฟล์/login ใน phase นี้
- **สร้าง memory engine เองใน harness** (ไม่ใช้ Mem0 — มันข้าม LLM failover/usage/provenance ของเรา และต้องมี vector DB); retrieval ใช้ **SQLite FTS5 (`tokenize='trigram'` รองรับภาษาไทยที่ไม่มีเว้นวรรค)** + filter หมวด/tag + rerank ด้วย importance/recency/success_rate; **ยังไม่มี embedding** (ออกแบบ `EmbeddingPort` ไว้เป็น follow-up)
- **5 Whys ถามใน chat** (research + portfolio session) ส่วนปุ่ม advisor ใน `/portfolio` ใช้ goal + memory ที่มีอยู่ (ไม่มี dialog)
- **ไม่ลบความจำแบบ in-place**: ขัดแย้ง = แถวใหม่ + แถวเก่า `superseded` (`valid_to`), ลบจริงเฉพาะผู้ใช้สั่ง "ลืม" จากหน้า Memory (Mem0 เองเลิกใช้ destructive UPDATE/DELETE ปี 2026 — ดู research §C.1)

## ต้องเสร็จก่อน
P07 (research workflow/agent loop), P12–P13 (portfolio advisor + UI), P16

## อ่านก่อนเริ่ม
- **`docs/research/advisor-styles-memory.md`** — ผลวิจัย (สไตล์กองทุน/นักลงทุนดัง, risk profiling, bias, 5 Whys rubric, Mem0/ReasoningBank, schema, prompt text, privacy) **prompt ทุกตัวใน phase นี้ต้องอิงจากไฟล์นี้**
- `backend/app/harness/state/{memory.py,memory_tools.py}` (KV store เดิม — **คงไว้** ไม่ย้าย `trade_decisions`), `backend/app/harness/storage/{models.py,db.py}`, `alembic/versions/0005_backtests.py`
- `backend/app/domain/workflows/{research.py,portfolio_advisor.py,trade_outcome.py,spec.py}`, `backend/app/harness/workflow/{research.py,portfolio_advisor.py,trading.py,runner.py}`
- `backend/app/harness/runtime/{research_agent.py,prompts.py,final_answer.py,agent_loop.py}`, `backend/app/api/routes/{sessions.py,portfolio.py,trade.py}`
- `backend/app/domain/agents/{library.py,schemas.py}` + `prompts/portfolio_parse_holdings.v1.md` (ตัวอย่าง prompt file ที่ใช้กับ `invoke_structured`)
- `backend/app/harness/structured/invoke.py`, `backend/app/domain/ports.py` (LLMPort, NodeContext ผ่าน `ctx.config`)
- frontend: `components/shell/Sidebar.tsx`, `components/conversation/{Turn,Answer,Composer,Conversation}.tsx`, `components/inspector/RunInspectorTabs.tsx`, `components/portfolio/{AdvisorPanel,PortfolioInspectorTabs}.tsx`, `stores/run-reducer.ts`, `lib/api.ts`, `tests/fixtures/`
- `docs/contracts.md` §10 (events) §11 (API) §12 (storage) — **17.0 ต้องเพิ่ม §13 Memory ก่อนเขียนโค้ด**

---

## สถาปัตยกรรม (F2: domain รู้ "นักลงทุน" / harness รู้ "SQLite + LLM")

```
app/domain/memory/                      ← ไม่ import framework; pure + pydantic + prompt files
  schemas.py      MemoryCategory, MemoryItemModel, ExtractedFacts, ReconcileOps, DiscoveryAssessment,
                  ReasoningItem, DistilledLessons, PrivacyVerdict, OutcomeJudgement
  ports.py        MemoryPort (Protocol) ที่ workflow node เรียกผ่าน ctx.config["memory"]
  profile.py      render_profile(items) → ข้อความ ≤ 600 tokens (pure)
  scrub.py        scrub_text(text) → (clean, hits[])  regex: email/phone/เลขบัตร 13 หลัก/เลขบัญชี/
                  จำนวนเงิน→bucket/% ปัดเป็น 5/ticker นอก allowlist→"{sector} large-cap"
  discovery.py    should_ask(assessment, whys_asked, skip) → "ask_only" | "research" | "research_then_ask"  (pure, ตาม research §B.3)
  prompts/        memory_extract.v1.md · memory_reconcile.v1.md · discovery_assess.v1.md ·
                  rb_distill.v1.md · rb_privacy_judge.v1.md · rb_outcome_judge.v1.md
app/harness/memory/                     ← SQLite + LLM (quick tier) + events
  store.py        UserMemoryStore: add/list/get/history/supersede/retire/confirm/forget/search_fts/touch
  engine.py       MemoryEngine (implements MemoryPort): recall(), learn(), guards แบบ deterministic
  bank.py         ReasoningBank: distill(), retrieve(), record_use(), settle(), list/retire
  tools.py        tool `user_memory_search` (category=memory, read_only)
```

กฎ D2 ยังอยู่: memory ถูกเขียน **เฉพาะใน node `learn`** (และจาก API ที่ผู้ใช้กดเอง) ไม่ใช่ side effect ของ LLM ระหว่าง loop

---

## Tasks

### 17.0 Contracts (ทำก่อน)
เพิ่ม `docs/contracts.md` **§13 Memory** โดยคัดลอก schema ตาราง (17.1), API (17.1/17.3), events (17.2) และ ports จากเอกสารนี้; §12 Storage เพิ่มตาราง + migration 0006; §11 เพิ่มแถว API; §10 เพิ่ม event ใหม่. `docs/README.md` เพิ่มแถว phase 17

### 17.1 Backend — user memory engine + API

**Migration `0006_memory.py`** (SQLite; FTS ผ่าน `op.execute`):

`user_memory`
| คอลัมน์ | ชนิด | หมายเหตุ |
|---|---|---|
| id | String(40) PK | `mem_<ULID>` |
| user_id | String(40) index | `usr_default` |
| category | String(32) | `investment_style, risk_tolerance, risk_capacity, goal, constraint, preference, life_fact, behavioral_pattern, knowledge_level` |
| key | String(120) nullable | slot ของ fact ที่มีค่าเดียว เช่น `risk_tolerance.score`, `style.primary`, `goal:house_2028`, `horizon.years`, `pace` — key ชนกัน = ขัดแย้ง |
| text | Text | ประโยคเดียวจบในตัว 15–80 คำ ขึ้นต้น "User …"/"ผู้ใช้ …" ภาษาเดียวกับผู้ใช้ |
| value_json | JSON nullable | เช่น `{"score":6,"scale":10}`, `{"amount":"2000000","currency":"THB","horizon_years":5}` |
| confidence | Float | user_stated 0.9, user_confirmed 0.95, inferred 0.5–0.7, questionnaire 0.9, portfolio_data 0.8 |
| importance | Integer 1–10 | ตั้งตอนเขียน (Generative Agents) |
| source | String(24) | `user_stated, user_confirmed, inferred_behavior, questionnaire, portfolio_data, manual` |
| source_ref | JSON | `{session_id?, message_id?, run_id?, prompt_version?}` |
| status | String(16) index | `candidate, active, superseded, retired, rejected` |
| supersedes / superseded_by | String(40) nullable | chain |
| valid_from / valid_to | DateTime(tz) | `valid_to` ใส่เมื่อ superseded/retired |
| sensitivity | String(12) | `normal, financial, pii` — `pii`/`financial` ห้ามไป ReasoningBank และ redact ใน log |
| created_at / updated_at / last_accessed_at | DateTime(tz) | |
| access_count | Integer | |

`user_memory_fts` = `CREATE VIRTUAL TABLE user_memory_fts USING fts5(id UNINDEXED, user_id UNINDEXED, text, tokenize='trigram')` + trigger หรือเขียนคู่กันใน store (เลือกเขียนคู่ใน store เพื่อให้ mypy/tests ง่าย — ต้องมี test ว่า FTS ตรงกับตารางหลักหลัง supersede/forget)

`reasoning_bank`
| คอลัมน์ | หมายเหตุ |
|---|---|
| id `rb_<ULID>`, title (≤120), description (1 ประโยค), content (≤150 คำ) | ตาม ReasoningBank |
| kind | `strategy` (จาก success), `pitfall` (จาก failure), `coaching` (จาก discovery/behavior) |
| outcome_signal | `success, failure, mixed` + `judge_score` Float |
| tags | JSON `{workflow: research\|portfolio\|trade, scenario?, asset_class?, market?: SET\|US\|FX\|CRYPTO, regime?, investor_style?, bias?, horizon?}` |
| use_count, success_count, success_rate | `success_rate = (success_count+1)/(use_count+2)` (Bayesian) คำนวณตอนอ่าน |
| source_count | จำนวน run/ผู้ใช้ที่ผลิต item ใกล้เคียง (dedupe ด้วย FTS score สูง + title เหมือน) — **k-anonymity gate ปิดไว้ก่อน** (`REASONING_BANK_MIN_SOURCES=1`) เพราะยัง single-user; เปิดเป็น 3 เมื่อ multi-user |
| status | `candidate, active, retired` — retire อัตโนมัติเมื่อ `use_count ≥ 10 และ success_rate < 0.35` |
| prompt_version, model, source_run_id, user_id (ผู้ผลิต — ใช้แค่ audit ไม่แสดงข้ามผู้ใช้), created_at | provenance |

`reasoning_bank_fts` (title, description, content; trigram) และ `reasoning_bank_uses (id, item_id, run_id, user_id, outcome nullable, created_at)` สำหรับ `settle`

**`domain/memory/schemas.py`** (Pydantic, ขอบเขตชัด):
- `ExtractedFacts{facts:[{category, key?, text, value_json?, confidence, importance, source: user_stated|inferred_behavior, sensitivity}]}` ≤ 5 ต่อ turn
- `ReconcileOps{ops:[{op: NOOP|ADD|UPDATE|SUPERSEDE|RETIRE, target_id?, text, value_json?, confidence, importance, reason}]}`
- `DiscoveryAssessment` = schema ใน research §B.4 (`request_type, root_goal_identified, root_goal{category, amount?, currency?, horizon_years?, text}, emotional_driver, confidence, next_question, stop_reason, advice_mode, memory_candidates[]`) + `answers_pending_question: bool`
- `DistilledLessons{items:[{title, description, content, kind, tags}]}` ≤ 3, `PrivacyVerdict{identifies_person_or_holdings: bool, reason}`, `OutcomeJudgement{success: bool, score 0–1, reason}`

**`harness/memory/engine.py` — `MemoryEngine(llm_quick, store, bank, emit?)`**
- `recall(user_id, query, *, workflow, tags) -> Recall{profile_text, items:[MemoryItem], reasoning:[ReasoningItem], provenance}`: profile = active items เรียง importance·confidence (≤ 600 tokens ผ่าน `render_profile`), items = FTS top-8 จาก query (กรอง status active/candidate — candidate ติดป้าย "(ยังไม่ยืนยัน)"), reasoning = `bank.retrieve(query, tags, k=2)`; `touch()` last_accessed/access_count
- `learn(user_id, *, user_messages:[str], assistant_text, discovery?, source_ref, observation_date) -> list[MemoryChange]`:
  1. `extract` (quick structured, prompt `memory_extract.v1`; input = ข้อความ **ผู้ใช้** ล่าสุด ≤ 10 ข้อความ + root goal ที่ยืนยันแล้วจาก discovery; ห้ามดึงจากข้อความ assistant) → ทิ้ง confidence < 0.4
  2. ต่อ fact: neighbours = active/candidate items ของ user หมวดเดียวกัน ที่ `key` ตรง **หรือ** FTS top-5
  3. **guards แบบ deterministic (ชนะ LLM):** `key` เดียวกัน + value ต่าง → `SUPERSEDE` ไม่ต้องถาม LLM; `inferred_behavior` ห้าม supersede `user_stated/confirmed` → เก็บเป็น `behavioral_pattern` แยก + flag `stated_vs_revealed`; fact หมวด `risk_tolerance/goal/constraint` ที่ไม่ใช่ `user_stated/confirmed` → `status=candidate`
  4. ไม่เข้า guard → quick structured prompt `memory_reconcile.v1` (ข้อความตาม research §C.4) → apply: NOOP touch / ADD insert / UPDATE+SUPERSEDE = แถวใหม่ + เก่า `superseded` / RETIRE
  5. structured ล้มเหลว → **ไม่เขียนอะไร** และคืน `[]` พร้อม warning (fail-safe ห้ามเดา)
  6. คืน `MemoryChange{op, item, previous?}` และ (ถ้ามี emit) ส่ง event `memory.updated`
- ทุก LLM call ใช้ `purpose="memory"` (usage แยก), prompt version ลง `source_ref`

**`harness/memory/bank.py` — `ReasoningBank`**
- `distill(run_summary, outcome: OutcomeJudgement|realised, tags, source_run_id, user_id)`: quick structured `rb_distill.v1` → ต่อ item: `scrub_text` → ถ้ามี hit หมวด pii → ทิ้ง; → quick structured `rb_privacy_judge.v1` → ผ่านจึง insert (`status=active` เมื่อ `source_count ≥ REASONING_BANK_MIN_SOURCES`); dedupe: title เหมือน (case-insensitive) หรือ FTS bm25 ของ title+description ใกล้มาก → `source_count += 1` แทน insert
- `retrieve(query, tags, k=2)`: filter `status=active` + tag `workflow` ตรง (และ `market`/`asset_class` ถ้ามี) → FTS top-10 (query = คำถาม + tags เป็นข้อความ) → rerank `bm25_norm × (0.5 + success_rate)` → top-k; คืน `[]` เมื่อไม่มี
- `record_use(item_ids, run_id, user_id)` และ `settle(run_id, success: bool)` → อัปเดต `success_count/use_count` ของ item ที่ run นั้นใช้ + auto-retire
- ทุก item ที่เข้า bank ห้ามมี: ชื่อคน/บัญชี/เลขบัตร, holdings/จำนวน/ต้นทุน/มูลค่าพอร์ต/รายได้, quote ตรงจากผู้ใช้, วันที่ผูกกับชีวิตส่วนตัว (research §C.5)

**Tool ใหม่ `user_memory_search(query, category?, limit≤10)`** (read_only, category `memory`) คืน items active ของ user ปัจจุบัน; `prompts.py` ส่วน `## Memory` ปรับเป็น: "โปรไฟล์ผู้ใช้อยู่ใน INVESTOR PROFILE แล้ว; ใช้ `user_memory_search` เมื่อต้องการรายละเอียดเพิ่ม (เช่น 'เคยบอกอะไรเรื่อง KBANK'); **ห้าม** เรียก `memory_update` เพื่อจำสิ่งที่ผู้ใช้พูด — ระบบจำให้เองหลังจบ turn" (tool `memory_update` เดิมยังอยู่สำหรับ namespace `decisions/research_notes`)

**API (`app/api/routes/memory.py`, prefix `/api`)** — ทุก endpoint รับ `X-User-Id` (default `usr_default`)
| Method | Path | ทำอะไร |
|---|---|---|
| GET | `/memory?status=active\|candidate\|all&category=` | `{items:[MemoryItemOut], counts:{active, candidate, superseded, retired}}` เรียง category → importance desc |
| GET | `/memory/profile` | `{profile_text, token_estimate, items_used:[id], updated_at}` (สิ่งที่ถูกฉีดเข้า prompt จริง) |
| POST | `/memory` `{category, text, key?, value_json?}` | ผู้ใช้พิมพ์เอง → ผ่าน reconcile (source `manual`, confidence 0.95) → `{changes:[MemoryChange]}` |
| PATCH | `/memory/{id}` `{text?, value_json?}` | แก้ = แถวใหม่ `supersedes` เดิม (source `manual`) → item ใหม่ |
| POST | `/memory/{id}/confirm` / `/memory/{id}/reject` | candidate → active (source → `user_confirmed`, confidence 0.95) / → `rejected` |
| POST | `/memory/{id}/retire` | active → retired (`valid_to=now`) |
| DELETE | `/memory/{id}?cascade=1` | **ลืมจริง** ลบแถว (+ทั้ง chain เมื่อ cascade) + FTS; 404 ถ้าไม่มี |
| GET | `/memory/{id}/history` | chain ทั้งเส้น (เก่า→ใหม่) |
| GET | `/reasoning-bank?workflow=&kind=&status=&q=&limit=` | `{items:[ReasoningItemOut (+success_rate, use_count)], counts}` |
| GET | `/reasoning-bank/{id}` | item + `uses` ล่าสุด 20 (run_id, outcome, created_at) |
| POST | `/reasoning-bank/{id}/retire` | ปิด item |
| GET | `/reasoning-bank/stats` | `{total, active, by_kind, by_workflow, avg_success_rate}` |

ID ของ memory/RB เพิ่มใน contracts §12 (`mem_`, `rb_`, `rbu_`). `pnpm gen:api` หลัง export OpenAPI

**Tests (ไม่มี network):** `scrub_text` ทุก regex (ไทย/อังกฤษ, เลขบัตร 13 หลัก, บัญชี 10–12 หลัก, "฿1,250,000"→bucket, "17%"→"15%", ticker allowlist); `render_profile` ตัดที่ 600 tokens เรียงความสำคัญ; store CRUD + FTS ไทย (`"ชอบลงทุนระยะยาว"` ค้นด้วย `"ระยะยาว"` เจอ) + supersede chain + forget cascade ลบ FTS; engine กับ FakeLLM: ADD, UPDATE, SUPERSEDE ("ชอบลงทุนเรื่อย ๆ" → "ชอบเร็ว ๆ": แถวเก่า superseded + valid_to, profile แสดงแค่ใหม่), RETIRE, NOOP, guard key-collision ไม่เรียก LLM, guard inferred-vs-stated, candidate สำหรับ risk_tolerance ที่ inferred, structured fail → ไม่เขียน; bank: distill→scrub→privacy judge (ปฏิเสธ item ที่มี "KBANK 2,000 หุ้น"), dedupe source_count, retrieve rerank, settle อัปเดต success_rate + auto-retire; API ทุก endpoint รวม 404/422 และ `X-User-Id` แยกข้อมูลสองค่า

### 17.2 Backend — 5 Whys + memory ใน research chat (workflow `research` v2)

graph ใหม่ (ทุก node ใหม่เป็น COMPUTE/AGENT ตาม spec; `reducers` เพิ่ม `memory`, `discovery`, `learned` แบบ replace):
```
plan → recall_memory → assess_discovery ─┬─ "ask_only"          → write_question → learn → END
                                         ├─ "research"          → retrieve_analyze → verify → synthesize_report → learn → END
                                         └─ "research_then_ask" → (เหมือน research แต่ synthesize_report ต่อท้ายคำถาม 1 ข้อ)
```
- `ResearchInput` เพิ่ม `skip_discovery: bool=false`, `user_id: str="usr_default"`; `PostMessage` เพิ่ม `skip_discovery`; `sessions.py` ส่ง `discovery` ปัจจุบันจาก `session.meta["discovery"]` เข้า input ด้วย
- **`recall_memory`** (COMPUTE): `memory.recall(user_id, query, workflow="research", tags={market จาก symbols ของ plan})` → state `memory = {profile_text, items:[{id, category, text, status}], reasoning:[{id, title, description}], provenance}`; emit `memory.recalled {items:[{id, category, text}], reasoning:[{id, title}]}`; `bank.record_use`
- **`assess_discovery`** (AGENT, quick structured `discovery_assess.v1`): input = คำถาม + history 10 ข้อความ + `discovery` เดิม (chain, whys_asked, root_goal) + โปรไฟล์ → `DiscoveryAssessment`. ถ้า memory มี goal ที่ตอบคำถามอยู่แล้ว → `stop_reason=memory_already_known` และ `next_question` เป็นการ **ยืนยัน** ("ยังเก็บเงินดาวน์คอนโดปี 2028 อยู่ไหม") ไม่ถามซ้ำ. `route_after_assess` ใช้ `domain/memory/discovery.should_ask()`:
  - `ask_only` เมื่อ `answers_pending_question` และยังไม่ identified และ `whys_asked < 5` และข้อความไม่มีคำขอข้อมูลใหม่ (request_type ของข้อความนี้ = `other`) และไม่ skip → ไม่ต้องจ่ายค่า research
  - `research_then_ask` เมื่อ request เป็น `buy_single|sell_all|concentrate|return_target|income` และ `confidence < 0.7` และ `whys_asked < 5` และไม่มี resistance/urgency และไม่ skip
  - ไม่งั้น `research` (รวม parse ไม่ได้ → `research`, `advice_mode=normal` — **ห้ามบล็อกผู้ใช้**); `urgency=high` → `advice_mode=stabilise` ส่งเข้า system prompt ("ให้ข้อเท็จจริงและทางเลือกก่อน อย่าถามต่อ")
  - state `discovery = {whys_asked, chain:[{q,a}], root_goal, root_goal_identified, confidence, next_question, stop_reason, advice_mode, request_type, memory_candidates}`; emit `discovery.assessed {whys_asked, root_goal_identified, confidence, stop_reason, advice_mode, next_question?, root_goal?: {text, category?}}` (frontend ใช้ `root_goal.text` ทำ chip "เข้าใจเป้าหมายแล้ว")
- **`write_question`** (REPORT): ไม่เรียก tool; LLM quick เขียนข้อความสั้น (สะท้อนสิ่งที่ผู้ใช้พูด + 1 คำถามเปิด + บอกว่าข้ามได้) → emit `answer.final {markdown, citations:[], unverified_numbers:[], discovery:{whys_asked, max:5, question}}`
- **`synthesize_report`**: รับ `memory.profile_text` + `reasoning` + `advice_mode` + (`research_then_ask` → `discovery.next_question`) → `final_answer.py` เพิ่มกติกา: "ตอบคำถามให้ครบก่อน แล้วปิดท้ายด้วยคำถาม 1 ข้อนี้คำต่อคำ (ไม่ตั้งคำถามอื่น)"; payload `answer.final` เพิ่ม `discovery?` เหมือน write_question
- **`ResearchAgent`**: system prompt เพิ่มบล็อก `## INVESTOR PROFILE (จาก memory — ใช้ปรับคำแนะนำ, อย่าเล่าซ้ำให้ผู้ใช้ฟังทั้งหมด)` + `## LESSONS FROM PAST RUNS` (≤ 2 items: title — content) + `advice_mode`; ฉีดผ่าน `AgentRunRequest.instruction`? **ไม่** — เพิ่ม field `context_blocks: list[str]` ใน `AgentRunRequest` (ports.py) ที่ agent ต่อท้าย system prompt (ไม่ปนกับ user turn เพื่อให้ microcompact ไม่เคลียร์ทิ้ง)
- **`learn`** (COMPUTE, `idempotent=False`): `memory.learn(...)` จากข้อความผู้ใช้ turn นี้ (+ root goal ที่ identified แล้วให้ใส่ `memory_candidates` เป็น fact `user_stated`) → emit `memory.updated {changes:[{op, id, category, text, previous_text?, status}]}`; จากนั้น **RB**: judge แบบไม่ใช้ LLM ก่อน — failure = `stop_reason` ใน LIMIT_KINDS หรือ `unverified_numbers > 3`; success = citations ≥ 1 และ unverified = 0 และ (discovery identified หรือ request_type=info); อื่น ๆ = ไม่กลั่น → `bank.distill(...)` ≤ 1 item สำหรับ research (`tags.workflow=research`, scenario=skill, market) → emit `reasoning.distilled {items:[{id, title, kind}]}`; `bank.settle(run_id, success)` สำหรับ item ที่ recall มา; ทั้งหมดห้ามทำให้ run fail (try/except → `warning memory_learn_failed`)
- `harness/workflow/research.py::_finish`: อัปเดต `session.meta["discovery"]` (reset chain เมื่อ `root_goal_identified` หรือ `stop_reason in (max_whys, resistance)` หลัง learn แล้ว) และ assistant message `meta` เพิ่ม `memory_changes`, `discovery`
- `GET /sessions/{id}` คืน `discovery` ปัจจุบันของ session

**Portfolio advisor (`portfolio_advisor` v2):** node `recall_memory` หลัง `compute_analytics` (profile + RB `workflow=portfolio` k=2) → `diagnose`/`propose_rebalance` เห็น `INVESTOR PROFILE` + `LESSONS` (ต่อจาก `_limits_text`); `AdviseInput.goal` ว่าง → ใช้ goal จาก memory (บอกใน report ว่าใช้เป้าหมายจากความจำ); node `learn` หลัง `report`: approved/edited → RB success (`distill` จาก diagnoses+proposal summary+policy), rejected พร้อม comment → RB failure + `memory.learn` จาก comment (เช่น "ไม่ขาย KBANK เด็ดขาด" → constraint candidate); `signature_fields` ไม่เปลี่ยน (memory ไม่อยู่ใน thread id — ถ้า resume จะ recall ใหม่)

**Trading:** `POST /trade/runs/{id}/outcome` หลัง reflection → `bank.distill` จาก reflection (`tags.workflow=trade, market, horizon`, `outcome_signal` จาก r_multiple) — ของเดิม (`trade_decisions` lessons) ไม่แตะ

**Events ใหม่ (contracts §10):** `memory.recalled`, `discovery.assessed`, `memory.updated`, `reasoning.distilled` (+ `answer.final.discovery?`) → `uv run python -m app.harness.events.export`

**Tests:** router ทุก branch ด้วย FakeLLM structured ตายตัว (ask_only / research / research_then_ask / parse fail → research / skip_discovery → research / whys_asked=5 → research / urgency → stabilise); chain สะสมข้าม 3 turns ผ่าน session meta แล้ว reset เมื่อ identified; learn เขียน memory และ event ลำดับถูก (`memory.recalled` ก่อน `llm.*`, `memory.updated` หลัง `answer.final`); learn ล้มไม่ทำให้ run fail; advisor recall ฉีด profile และ learn หลัง approve/reject; fixtures `tests/fixtures` ของ research api อัปเดต; `test_research_workflow` event sequence เดิมยังผ่าน (node ใหม่แทรก)

### 17.3 Frontend — หน้า Memory + chat UI

- **Sidebar**: nav `Memory` (icon `Brain`) → `/memory`
- **`app/(shell)/memory/page.tsx` + `components/memory/`**:
  - `MemoryProfileCard`: ข้อความโปรไฟล์ที่ถูกฉีดจริง (`GET /memory/profile`) + token estimate + "AI เห็นสิ่งนี้ทุกครั้งที่คุยกับคุณ"
  - `MemoryList`: group ตาม category (ชื่อไทย: สไตล์การลงทุน · การรับความเสี่ยง · ความสามารถรับความเสี่ยง · เป้าหมาย · ข้อจำกัด · ความชอบ · ข้อมูลชีวิต · พฤติกรรม · ระดับความรู้) แต่ละ item: text, badge source (คุณบอกเอง / ยืนยันแล้ว / AI อนุมาน / กรอกเอง), confidence bar, วันที่, เมนู: ยืนยัน (candidate), แก้ (inline → PATCH), เลิกใช้ (retire), **ลืม** (DELETE confirm dialog "ลบถาวรรวมประวัติ"), ประวัติ (drawer แสดง chain เก่า→ใหม่ พร้อม valid_from/to)
  - section "รอยืนยัน" (candidate) เด่นด้านบน; toggle "แสดงที่เลิกใช้แล้ว"
  - `AddMemoryForm`: เลือกหมวด + พิมพ์ประโยค → POST → แสดง changes ที่เกิด (เช่น "แทนที่: …")
  - tab `ReasoningBank`: ตาราง title · kind (badge strategy/pitfall/coaching) · workflow · tags · use_count · success_rate (bar) · created; filter workflow/kind/q; drawer รายละเอียด content + uses; ปุ่ม retire; header stats
- **Chat (`/research`)**:
  - `MemoryChip` ใน `Turn` เมื่อ `memory.updated`: "จำแล้ว: ชอบลงทุนระยะสั้น" / "อัปเดต: ระยะยาว → ระยะสั้น" / "เลิกใช้: …" คลิก → `/memory` ; ปุ่ม `เลิกจำ` (POST retire) บนชิป
  - `DiscoveryCard` ใต้คำตอบเมื่อ `answer.final.discovery` มี: progress "คำถาม 2/5 เพื่อเข้าใจเป้าหมาย" + ปุ่ม **"ข้าม — วิเคราะห์เลย"** (ส่งข้อความถัดไปพร้อม `skip_discovery: true` — Composer เก็บ flag และแสดง pill "ข้ามคำถาม") ; เมื่อ `discovery.assessed.root_goal_identified` → chip "เข้าใจเป้าหมายแล้ว: …"
  - inspector tab `Memory`: items ที่ recall + RB items ที่ใช้ใน run นี้ + changes หลัง learn (จาก events)
  - `run-reducer`: เพิ่ม `memory: {recalled, reasoning, changes, discovery}` (idempotent ตาม seq)
- **Portfolio `AdvisorPanel`**: บรรทัด "ใช้โปรไฟล์ของคุณ: <สรุป 1 บรรทัดจาก /memory/profile>" + link; หลัง approve/reject แสดง `reasoning.distilled` เป็น toast "บันทึกบทเรียนลง ReasoningBank"
- `lib/api.ts` + `pnpm gen:api`; fixtures อัดใหม่ (`RECORD_FE_FIXTURES=1`) รวม `research-discovery.jsonl` (3 turns: ask → ask → identified)
- Tests (vitest): Memory page (list/confirm/edit/retire/forget/history/add), ReasoningBank tab, MemoryChip, DiscoveryCard + skip flag ส่ง body ถูก, reducer events ใหม่, inspector tab

### 17.4 Docs
- `docs/contracts.md` §13 (17.0), `docs/ui-spec.md` เพิ่ม §Memory + chat chips, `docs/architecture.md` เพิ่ม `domain/memory`, `harness/memory`, หน้า `/memory`, `docs/README.md` แถว 17, `CLAUDE.md`: ตาราง phase + โครงสร้างโฟลเดอร์ + Commands (ถ้ามี)
- `.env.example`: `MEMORY_ENABLED=true`, `MEMORY_PROFILE_MAX_TOKENS=600`, `REASONING_BANK_MIN_SOURCES=1`, `DISCOVERY_MAX_WHYS=5`, `DISCOVERY_CONFIDENCE=0.7` (อ่านใน `settings.py`; Settings page ยังไม่ต้องมี section ใหม่)

---

## Definition of Done

- [ ] backend: `ruff check`, `ruff format --check`, `mypy --strict`, `lint-imports` (domain/memory ไม่ import framework), `pytest` ผ่านทั้งหมด (ยกเว้น fail เดิม `test_unconfigured_providers_no_network` FRED ตัวเดียว)
- [ ] migration 0006 รันบน DB เดิมได้ (มี data ของ phase ก่อน) และ FTS ค้นภาษาไทยได้
- [ ] live (LLM จริง, บันทึกคำสั่ง/ผล): (1) บอก "ผมชอบลงทุนเรื่อย ๆ ระยะยาว" → `/memory` เห็น item `active`; บอก "ตอนนี้ไม่ชอบแล้ว ชอบเร็ว ๆ" → item เก่า `superseded` และ profile แสดงอันใหม่ (2) "ผมอยากซื้อ NVDA" → คำตอบมีข้อมูลราคา/ข่าว **และ** จบด้วยคำถาม why 1 ข้อ + DiscoveryCard 1/5; ตอบ 2–3 ครั้งจน identified → chip เป้าหมาย + memory goal ถูกบันทึก; กด "ข้าม" → ไม่ถามต่อ (3) run advisor ใน `/portfolio` → report อ้างโปรไฟล์; approve → `/memory` tab ReasoningBank มี item ใหม่ไม่มีชื่อหุ้น/จำนวน; run ซ้ำ → `memory.recalled` มี RB item นั้น (4) กด "ลืม" → หายจาก list, history และ FTS
- [ ] ไม่มี PII/holdings ใน reasoning_bank (test + ตรวจมือจาก live run); key/token ไม่โผล่ใน log
- [ ] frontend: `pnpm lint`, `pnpm typecheck`, `pnpm test` ผ่าน; `pnpm gen:api` แล้ว; หน้า `/memory` ทำงานที่ 1440/390 px
- [ ] `docs/contracts.md` §13 ตรงกับโค้ด; อัปเดตตาราง phase ใน `CLAUDE.md`

## Follow-ups (ไม่ทำใน phase นี้)
- multi-user จริง: ตาราง `users` + ตัวเลือกโปรไฟล์ + เปิด `REASONING_BANK_MIN_SOURCES=3` (k-anonymity)
- `EmbeddingPort` (Ollama `nomic-embed-text` / Gemini) + hybrid retrieval (FTS + cosine) แทน FTS อย่างเดียว
- risk profiling 3 แกน (tolerance / capacity / need) เป็น questionnaire + compute `required_return` (research §A.4); style playbook เป็น data file + policy caps (research §A.2)
- guard "เปลี่ยน risk tolerance ภายใน 30 วันหลัง drawdown > 10% → candidate" (ต้องต่อ analytics MDD)
- lot size หุ้นไทย ≥ THB 500 = 50 หุ้น (research §A.5) และกองทุนลดหย่อนภาษีเป็น locked position
- settle RB จาก P&L จริงของแผนที่ apply แล้ว (เทียบ snapshot รายสัปดาห์) นอกเหนือจาก approve/reject
