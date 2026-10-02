# Phase 06 — Context management (D1) ⭐ pillar สำคัญที่สุดของ Dexter

## เป้าหมาย
ทำให้ agent ทำงานยาวได้โดย context ไม่ระเบิด (200k → 400k → 💥) โดยมี 3 ชั้น
1. **Microcompact** ทำทุก turn โดยไม่ใช้ LLM: ล้างเนื้อหา tool result เก่าออก
2. **Full compaction** ทำเมื่อเกิน threshold: ให้ fast LLM สรุปแบบมีโครงสร้าง
3. **Fallback truncate** ทำเมื่อ compaction ล้ม: ตัดรอบเก่า โดยเก็บ tool use ล่าสุดไว้ 5 ชิ้น

ก่อน compact มี **memory flush** (optional): ให้ LLM เลือกเก็บข้อเท็จจริงสำคัญลง persistent memory

## ต้องเสร็จก่อน
P05

## อ่านก่อนเริ่ม (ต้องอ่านทั้งไฟล์)
- `../dexter/src/agent/microcompact.ts`
- `../dexter/src/agent/compact.ts` (prompt ทั้ง 9 หัวข้อ, `NO_TOOLS_PREAMBLE`, การ parse `<analysis>`/`<summary>`)
- `../dexter/src/agent/agent.ts` ส่วน `manageContextThreshold`, `compactMessages`, `truncateMessages`
- `../dexter/src/utils/tokens.ts`
- `../dexter/src/agent/token-counter.ts`
- `../dexter/src/memory/flush.ts`
- `../deepseek-harness/packages/compaction/compaction-basic/src/` (config.ts, region.ts, summarizer.ts) เพื่อดูการเลือกช่วง message ที่จะสรุป

## ค่าคงที่ (port ตรงจาก Dexter; override ได้ใน settings)

| ค่า | Default | ที่มา |
|-----|---------|-------|
| `MC_CLEARED_MESSAGE` | `"[Old tool result content cleared — full result in scratchpad call_id={id}]"` | microcompact.ts (เพิ่ม call_id) |
| `MC_COUNT_TRIGGER` | 8 | `COUNT_TRIGGER_THRESHOLD` |
| `MC_KEEP_RECENT` | 4 | `COUNT_KEEP_RECENT` |
| `MC_TOKEN_TRIGGER` | 80_000 | `TOKEN_TRIGGER_THRESHOLD` |
| `COMPACTABLE_CATEGORIES` | `data, search, memory` (tool ที่ read_only) | `COMPACTABLE_TOOLS` → ใช้ `ToolSpec.category` + `read_only` แทนการเขียนชื่อตายตัว |
| `MAX_OUTPUT_TOKENS_FOR_SUMMARY` | 20_000 | tokens.ts |
| `AUTOCOMPACT_BUFFER_TOKENS` | 13_000 | tokens.ts |
| `DEFAULT_CONTEXT_WINDOW` | 128_000 | tokens.ts |
| threshold | `context_window(model) − 20_000 − 13_000` | `getAutoCompactThreshold` |
| `KEEP_TOOL_USES` | 5 | tokens.ts |
| `MAX_CONSECUTIVE_COMPACTION_FAILURES` | 3 | compact.ts |
| `MIN_TOOL_RESULTS_FOR_COMPACTION` | 3 | compact.ts |
| chars per token (fallback) | 3.5 | `estimateTokens` |

## Tasks

### 6.1 Token accounting (`app/harness/context/tokens.py`)
- `estimate_tokens(text) = ceil(len(text) / 3.5)` ใช้เป็น fallback เท่านั้น
- `TokenCounter` สะสม usage ต่อ run แยกตาม purpose (agent/final/compact/structured)
- `current_context_tokens(ctx, messages)`: ถ้า `ctx.last_api_input_tokens > 0` ให้ใช้ค่านั้น (ค่าจริงจาก API response ล่าสุด) ถ้าไม่มีค่อยประมาณจาก messages
- `compact_threshold(model)` ใช้ context window จาก `model_catalog` (P04)
- emit `context.usage{used_tokens, context_window, percent}` หลังทุก LLM call

### 6.2 Microcompact (`app/harness/context/microcompact.py`)
`microcompact(messages, *, tools: ToolRegistry, scratchpad) -> MicrocompactResult(messages, cleared, tokens_saved, trigger)`
- หา index ของ tool message ที่ compactable และยังไม่ถูก clear
- **count trigger:** ถ้าจำนวนเกิน 8 ให้ clear ทุกตัวยกเว้น 4 ตัวล่าสุด
- **token trigger** (เช็กเมื่อ count ไม่ trigger): ถ้าผลรวม estimate เกิน 80k ให้ clear ทุกตัวยกเว้น 4 ตัวล่าสุด
- เปลี่ยน content เป็น `MC_CLEARED_MESSAGE` (ใส่ call_id) แต่คง `tool_call_id` ไว้เพื่อให้ message pairing ของ provider ยังถูกต้อง
- `scratchpad.mark_cleared(call_ids)` แล้ว emit `context.microcompact` เมื่อ cleared > 0
- เป็น pure function (คืน list ใหม่ ไม่ mutate)

### 6.3 Full compaction (`app/harness/context/compact.py`)
- port prompt ทั้งชุด: `NO_TOOLS_PREAMBLE` + `ANALYSIS_INSTRUCTION` + `BASE_COMPACT_PROMPT` 9 หัวข้อ (Original Query and Intent, Key Concepts, Data Retrieved, Errors and Retries, Analysis Progress, **Numerical Data**, Pending Data Needs, Current Work State, Recommended Next Steps)
- ปรับให้เป็นบริบทการเงินของเรา:
  - หัวข้อ 2 ให้ระบุ symbol ในรูป canonical
  - หัวข้อ 6 ให้ตัวเลข **ทุกตัว** แนบ `provenance_id` ที่ได้จาก tool result (เพื่อให้การอ้างอิงยังใช้ได้หลัง compact)
  - รองรับภาษาไทย: สรุปด้วยภาษาเดียวกับคำถามผู้ใช้
- input ของ summarizer = query + ผลเต็มของ tool **จาก scratchpad** (ไม่ใช่ message ที่ถูก microcompact ไปแล้ว) ตามแบบ `ctx.scratchpad.getToolResults()`
- เรียก LLM tier `quick` ด้วย purpose `compact` โดยไม่ bind tools
- parse `<summary>` (ถ้าไม่มีให้ใช้ทั้งข้อความ) และถ้ามีการเรียก tool หรือ summary ว่างให้ถือว่าล้ม
- `compact_messages(messages, summary, query)` ตามแบบ `compactMessages`: คง system prompt → ใส่ user message ที่มี query เดิม + `"<context_summary>…</context_summary>"` → ตัด message ก่อนหน้าทิ้ง
- บันทึก `scratchpad.append(compaction, summary, pre, post)` แล้ว emit `context.compaction{phase:"start"}` และ `{phase:"end", pre_tokens, post_tokens, summary}`

### 6.4 Orchestrator (`app/harness/context/manager.py`)
`ContextManager.before_llm_call(ctx, messages) -> messages` เรียกก่อนทุก LLM call ใน agent loop (P07) มีลำดับดังนี้
1. microcompact
2. คำนวณ tokens แล้วถ้า ≤ threshold ให้คืน
3. **memory flush** (ถ้า `memory_enabled` และยังไม่ flush ในรอบนี้): เรียก LLM quick ให้เลือกข้อเท็จจริงที่ควรจำข้าม session (เช่น ความชอบของผู้ใช้, ข้อสรุปเกี่ยวกับพอร์ต) แล้วเขียนลง `memory` namespace `research_notes` (port `memory/flush.ts`) ถ้าล้มให้ข้าม
4. full compaction ถ้า `failures < 3` และ `active_tool_results ≥ 3` ถ้าสำเร็จให้ reset failures ถ้าล้มให้ failures += 1 และ emit `phase:"failed"`
5. fallback: `truncate_messages(messages, keep_tool_uses=5)` ตัดทั้งรอบ (assistant tool_calls + tool results) จากเก่าสุด โดยคง system และ user message แรกไว้ แล้ว emit `context.compaction{phase:"fallback_clear", cleared, kept}`
- ห้ามทำให้คู่ assistant(tool_calls) ↔ tool message แยกกัน (provider จะ error) ให้มี validator `assert_tool_pairing(messages)` ใช้ใน test และ debug mode

### 6.5 Tests (`tests/harness/context/`)
- microcompact: 9 tool results ต้อง clear 5 เหลือ 4, token trigger ด้วยข้อความยาว, tool ที่ไม่ compactable ต้องไม่โดน clear, pairing ยังถูกต้อง และ call_id อยู่ใน marker
- threshold: model ที่ window 200k → threshold 167k และ model ที่ไม่รู้จัก → 95k
- manager + FakeLLM:
  - สร้าง usage ปลอม `input_tokens=180k` แล้วต้องเกิด compaction และได้ messages = system + user(summary)
  - FakeLLM compaction raise 3 ครั้ง แล้วครั้งที่ 4 ต้องไม่พยายาม compact อีก แต่ fallback truncate แทน
  - active tool results < 3 ต้องข้ามไป truncate
  - summary ต้องมี provenance id ที่อยู่ใน scratchpad (ตรวจด้วย FakeLLM ที่ echo input)
- snapshot test ของ compaction prompt (เพื่อไม่ให้ prompt เปลี่ยนโดยไม่ตั้งใจ)

## Definition of Done
- [ ] ทุก constant อยู่ที่เดียว (`context/config.py`) และ override ผ่าน settings ได้
- [ ] event `context.*` ครบตาม contracts และมี payload ถูกต้อง
- [ ] หลัง compaction ผลเต็มของ tool ยังอยู่ใน scratchpad (ตรวจใน test)
- [ ] coverage ของ `app/harness/context` ≥ 95%

## นอกขอบเขต
UI ของ compaction (P08)
