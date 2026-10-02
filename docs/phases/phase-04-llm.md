# Phase 04 — LLM layer + structured output (F4, T5)

## เป้าหมาย
สร้าง `LLMPort` ที่ domain เรียกใช้โดยไม่รู้ provider รองรับหลาย provider, แยก deep/quick tier, retry, failover, รายงาน token/cost และมี structured output พร้อม fallback + coercion ตามแบบ TradingAgents รวมทั้งมี **FakeLLM** สำหรับ test ของทุก phase ถัดไป

## ต้องเสร็จก่อน
P01

## อ่านก่อนเริ่ม
- `docs/contracts.md` §7 (LLM port), §8 (Decision)
- `../TradingAgents/tradingagents/llm_clients/` ทั้งโฟลเดอร์ (factory.py, base_client.py, capabilities.py, model_catalog.py, validators.py, api_key_env.py, anthropic_client.py, openai_client.py, google_client.py)
- `../TradingAgents/tradingagents/agents/structured.py`, `schemas.py` (โดยเฉพาะ `_coerce_optional_float`)
- `../dexter/src/model/llm.ts` (withRetry, isNonRetryableError, callLlm, stream, FAST_MODELS, Anthropic cache_control)
- `../dexter/src/providers.ts`
- `../deepseek-harness/packages/llm/` (ดูการแยก provider)

## Tasks

### 4.1 Dependencies
`langchain-core`, `langchain-anthropic`, `langchain-openai`, `langchain-google-genai` (ใช้ **เฉพาะใน `app/harness/llm/`**) และ provider ที่ OpenAI-compatible (DeepSeek, OpenRouter, Ollama, xAI) ให้ใช้ `langchain-openai` + `base_url`

### 4.2 Config (`app/harness/llm/config.py`)
- `LLMTierConfig(provider, model, temperature?, max_tokens?, effort?)` ของ `deep` และ `quick`
- `failover: list[LLMTierConfig]` ต่อ tier
- ค่า default (override ได้ด้วย env/settings):
  - Anthropic: deep = `claude-sonnet-5`, quick = `claude-haiku-4-5-20251001` (งานที่ต้องคิดหนักมากเปลี่ยน deep เป็น `claude-opus-5-5` ได้)
  - OpenAI, Google, DeepSeek: ให้ใส่ model ใน `.env` เอง (`LLM_DEEP_PROVIDER`, `LLM_DEEP_MODEL`, `LLM_QUICK_PROVIDER`, `LLM_QUICK_MODEL`, `LLM_FAILOVER=openai:gpt-...,deepseek:deepseek-chat`)
- `.env.example` เพิ่ม `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY`, `OLLAMA_BASE_URL`
- `model_catalog.py`: context window + ราคา/1M tokens (in/out/cache) ต่อ model ที่รองรับ สำหรับคำนวณ `cost_usd` และ threshold ของ compaction (P06) ถ้า model ไม่รู้จักให้ใช้ window 128k และราคา 0 พร้อม warning

### 4.3 Clients (`app/harness/llm/`)
- `base.py`: `BaseLLMClient` ที่มี `complete()`, `stream()`, `bind_tools()` และ `with_structured()` (ห่อ LangChain chat model)
- `factory.py`: `create_client(cfg)` import provider แบบ lazy (port จาก factory.py) และถ้าไม่มี key ให้ raise `LLMNotConfigured`
- `retry.py`: port `withRetry` ของ Dexter คือ 3 ครั้ง, `500ms·2ⁿ` และ **ไม่ retry** เมื่อเจอ auth/invalid request/context too long (port `isNonRetryableError` + `classifyError`)
- `failover.py`: `FailoverLLM(tier)` ลอง client ตามลำดับ และเปลี่ยนตัวเมื่อ retry หมดหรือเจอ `LLMNotConfigured`/5xx/overloaded (ไม่เปลี่ยนเมื่อเจอ validation error) โดยทุกครั้งที่เปลี่ยนต้องส่ง event `warning{code:"llm_failover"}` (ตอนนี้ใช้ callback ส่วน P05 จะต่อเข้า event bus)
- `usage.py`: ดึง `input_tokens`, `output_tokens` และ `cache_read_tokens` จาก response metadata ของแต่ละ provider (ต้องใช้ตัวเลขจริงจาก API เพราะ D1 ใช้ค่านี้) แล้วคำนวณ cost
- Anthropic: ใส่ `cache_control` ที่ system prompt (ตาม Dexter)
- `adapter.py`: `HarnessLLM(LLMPort)` แปลง `LLMMessage` ↔ LangChain messages และ `ToolSpec` → tool schema (จาก `args_model.model_json_schema()`)

### 4.4 Structured output (`app/harness/structured/`)
port `structured.py`:
- `bind_structured(llm, schema)` → structured runnable หรือ `None` ถ้า provider ไม่รองรับ (log warning ครั้งเดียว)
- `invoke_structured(messages, schema)`:
  1. native structured → `StructuredResult(mode="native")`
  2. ถ้าล้ม → เรียก free-text พร้อมแนบ JSON schema ในคำสั่ง แล้ว extract JSON block + `schema.model_validate` → `mode="fallback_parsed"`
  3. ถ้ายังล้ม → `StructuredResult(value=None, mode="failed", raw_text=...)` **ห้าม raise** เพื่อให้ router ส่งต่อเป็น REVIEW
- `coerce.py`: port `_coerce_optional_float` (N/A → None, "$1,234.50" → 1234.5, "15%" ในช่องราคา → None, range/hedge → None) และ `coerce_percent_field()` ("15%" → 0.15 สำหรับช่องที่เป็นสัดส่วน) โดยใช้เป็น `field_validator(mode="before")` ใน schema ของ domain (ตัว coerce อยู่ใน `domain/models/coerce.py` เพราะเป็น logic ของ domain)
- ค่าคงที่ `NO_EXTERNAL_TOOLS` ให้ใส่ใน prompt ของ structured call

### 4.5 FakeLLM (`app/harness/llm/fake.py`)
- `FakeLLM(script: list[FakeTurn])` โดย `FakeTurn` เป็นได้ทั้ง text, tool_calls, structured object หรือ exception และจับ `purpose`/`tier` ของทุกครั้งที่เรียกเพื่อ assert ได้ รวมถึงรายงาน usage ปลอม (input tokens กำหนดได้ เพื่อใช้ test compaction ใน P06)
- ใช้ใน test ทุก phase ถัดไป

### 4.6 `/api/config/status` เพิ่มส่วน LLM
`{llm: {deep: {provider, model, configured}, quick: {...}, failover: [...]}}`

### 4.7 Tests
- factory: provider ที่ไม่มี key ต้อง raise `LLMNotConfigured`
- retry: 429 retry แล้วสำเร็จ, 401 fail ทันที
- failover: client แรก raise overloaded แล้วต้องไปใช้ client ที่สอง และมี warning callback
- structured: native OK / native fail แต่ fallback parse OK / ล้มทั้งคู่ต้องได้ mode=failed, value=None
- coercion: ตารางเคสอย่างน้อย 15 เคส
- `route_decision(None)` → REVIEW และ `route_decision(conf=0.3, APPROVE)` → REVIEW
- live test (`-m live`) เรียก model จริง 1 ครั้งต่อ provider ที่มี key

## Definition of Done
- [ ] ไม่มี `langchain` import นอก `app/harness/`
- [ ] ใส่ `ANTHROPIC_API_KEY` อย่างเดียวแล้ว `uv run python -m app.harness.llm.smoke "สวัสดี"` ตอบได้ พร้อมพิมพ์ tokens และ cost
- [ ] structured fallback ไม่ทำให้ pipeline พังในทุกกรณี
- [ ] FakeLLM ใช้งานได้และมีตัวอย่างใน docstring

## นอกขอบเขต
agent loop (P07) และ compaction (P06)
