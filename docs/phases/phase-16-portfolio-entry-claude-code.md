# Phase 16 — กรอกพอร์ตให้ง่ายเหมือนแอปโบรกเกอร์ + LLM provider "Claude (subscription ผ่าน Claude Code)"

## เป้าหมาย

1. **Portfolio entry (MVP #3 ต่อยอด):** ผู้ใช้ต้องการ "copy พอร์ตจริงจากแอปโบรกเกอร์มาไว้ในแพลตฟอร์ม" ให้ AI วิเคราะห์ แต่แอปโบรกเกอร์แต่ละเจ้าแสดงตัวเลขไม่เหมือนกัน (บางเจ้าโชว์จำนวน+ต้นทุนเฉลี่ย, บางเจ้าโชว์มูลค่า+%กำไร, บางเจ้าโชว์จำนวน+กำไรเป็นบาท) ระบบจึงต้องรับได้ทุกแบบและ **คำนวณส่วนที่เหลือให้เอง** (F1: compute ไม่ใช่ LLM) พร้อม preview ก่อนบันทึก และวางข้อความจากแอปให้ AI แปลงเป็นแถวได้
2. **LLM provider ใหม่ `claude_code`:** ใช้ Claude ผ่าน **Claude subscription** ของผู้ใช้ โดยเรียก Claude Code binary ที่ติดตั้งอยู่ในเครื่อง (ไม่แก้ไข) ในโหมด headless `claude -p` — เลือก model ได้ (Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.1) และเข้า failover chain ได้เหมือน provider อื่น

## ต้องเสร็จก่อน
P13 (portfolio UI), P04 (LLM layer)

## ข้อเท็จจริงที่ตรวจแล้ว (2026-10-14)

- `.env` มี `ANTHROPIC_API_SUB_KEY=sk-ant-oat01-…` — นี่คือ **OAuth token ของ Claude Code** (จาก `claude setup-token`) ไม่ใช่ API key: ส่งเข้า `api.anthropic.com/v1/messages` ตรงๆ ไม่ได้ (ถูกบล็อกฝั่ง server ตั้งแต่ต้นปี 2026) และ [legal page ของ Claude Code](https://code.claude.com/docs/en/legal-and-compliance) ระบุว่า OAuth token ใช้ได้เฉพาะ "ordinary use of Claude Code and other native Anthropic applications" ส่วน developer ที่สร้าง product ต้องใช้ API key
- ทางที่ยังอยู่ในกรอบ: **ให้ผู้ใช้ล็อกอิน Claude Code binary ที่ไม่ได้แก้ไขด้วย subscription ของตัวเอง** แล้วแพลตฟอร์มเรียก binary นั้นในโหมด headless (เอกสาร Agent SDK ระบุเองว่า "run the CLI as a subprocess with the `-p` flag and `--output-format json`" เป็นวิธีที่รองรับ) → provider นี้จึงเป็น **เครื่องมือส่วนตัวบนเครื่องของผู้ใช้เอง** ห้ามนำไปให้ผู้ใช้คนอื่นใช้ผ่าน token ของเรา (ถ้าจะ deploy ให้คนอื่น ต้องให้เขาใช้ `ANTHROPIC_API_KEY` ของเขาเอง = provider `anthropic` เดิม)
- ทดสอบจริงแล้ว: `claude -p "..." --model claude-haiku-5-5 --output-format json --tools "" --no-session-persistence` ด้วย `CLAUDE_CODE_OAUTH_TOKEN=<token>` (และ unset `CLAUDECODE`, `ANTHROPIC_API_KEY`) ตอบใน 2.4 s ได้ JSON `{result, stop_reason, usage{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}, total_cost_usd, modelUsage, is_error, subtype}`
- flag ที่มีใน Claude Code 2.1.91: `--system-prompt`, `--json-schema <schema>` (structured output), `--effort low|medium|high|max`, `--model`, `--tools ""`, `--no-session-persistence`, `--setting-sources`, `--strict-mcp-config`, `--disable-slash-commands`, `--max-budget-usd`, `--output-format json|stream-json`, `--fallback-model`. **ห้ามใช้ `--bare`** (bare ปิด OAuth)
- ข้อจำกัด: CLI ไม่คืน `tool_use` block ของ tool ที่เรากำหนดเอง → tool calling ต้อง **จำลองผ่าน `--json-schema`** (ดู §16.1)

## อ่านก่อนเริ่ม
- `backend/app/harness/llm/{config,factory,base,failover,usage,errors,model_catalog}.py` และ `tests/harness/llm/{stubs,test_config_factory,test_structured}.py`
- `backend/app/harness/structured/invoke.py` (native → fallback parse) และ `backend/app/harness/llm/adapter.py` (LLMPort → client, tool_calls)
- `backend/app/domain/portfolio/entry.py`, `backend/app/domain/compute/portfolio.py` (`units_from_value`), `backend/app/api/routes/portfolio.py` (`_write`, `import_csv`), `tests/domain/portfolio/test_entry_apply_summary.py`
- `frontend/lib/holdings.ts`, `frontend/components/portfolio/{HoldingsTable,ImportCsvDialog,PortfolioFormDialog}.tsx`, `frontend/app/(shell)/portfolio/PortfolioView.tsx` (EmptyState, BookDetail), `frontend/components/trade/SymbolSearch.tsx`
- `docs/contracts.md` §5 (Portfolio), §7 (LLM port), §11 (HTTP API) — **แก้ก่อนเขียนโค้ด** (แก้แล้วใน phase นี้)

---

## Tasks

### 16.1 Backend — provider `claude_code` (`app/harness/llm/`)

**ไฟล์ใหม่ `app/harness/llm/claude_code.py`** — LangChain `BaseChatModel` subclass `ClaudeCodeChatModel` (harness/llm ใช้ LangChain ได้ตาม F2) ที่ทุก call = spawn `claude` หนึ่ง process:

- resolve binary: `settings.claude_code_bin` ถ้าตั้ง ไม่งั้น `shutil.which("claude")` (Windows: `claude.cmd`/`claude.exe` ก็ต้องเจอ) → ไม่เจอ = `LLMNotConfigured("Claude Code CLI not found; install Claude Code and run `claude setup-token`")`
- env ของ subprocess = copy ของ `os.environ` **ลบ** `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` แล้ว **ใส่** `CLAUDE_CODE_OAUTH_TOKEN=<settings.anthropic_api_sub_key>` เมื่อมี (ถ้าไม่มี token ให้พึ่ง login ที่มีอยู่ในเครื่อง) — token ห้ามโผล่ใน log/provenance/exception message
- args คงที่: `-p --output-format json --tools "" --no-session-persistence --disable-slash-commands --strict-mcp-config --setting-sources "" --permission-mode dontAsk --model <cfg.model>` + `--effort <cfg.effort>` เมื่อมี + `--max-budget-usd <settings.run_max_cost_usd>` + `--system-prompt <system>` (ข้อความ system รวมทุก SystemMessage นำหน้า; ถ้าไม่มีให้ส่ง system สั้นๆ ที่ไม่ใช่ของ Claude Code) และ cwd = โฟลเดอร์ว่างชั่วคราวใต้ `DATA_DIR/claude_code/` (กัน CLAUDE.md ของโปรเจกต์หลุดเข้า context)
- prompt ส่งทาง **stdin** (ไม่ใช่ argv — Windows มี limit ความยาว args และห้าม token/ข้อความหลุดใน process list): แปลง conversation (user/assistant/tool messages) เป็น transcript ข้อความเดียวตามรูปแบบที่ชัดเจน (เช่น `[user]…`, `[assistant]…`, `[tool_result name=…]…`) โดย message สุดท้ายคือสิ่งที่ต้องตอบ
- **Tool calling แบบจำลอง** (ใช้เมื่อ `bind_tools` ถูกเรียก): ใส่รายการ tool (name, description, JSON schema ของ args) ลงท้าย system prompt และส่ง `--json-schema` ของ envelope `{"text": string, "tool_calls": [{"name": string, "args": object}]}` → แปลงเป็น `AIMessage(content=text, tool_calls=[{id: "call_<uuid>", name, args}])`; tool ที่ไม่รู้จัก/args ไม่ใช่ object → ทิ้งและใส่ warning ใน `response_metadata`
- **Structured output:** `with_structured_output(schema, method="json_schema", include_raw=True)` → ส่ง `--json-schema <schema.model_json_schema()>` แล้วอ่าน `structured_output` จาก result JSON (ถ้า CLI รุ่นนั้นไม่มี field นี้ ให้ parse `result` ด้วย `extract_json_object`); คืน `{"raw": AIMessage, "parsed": schema|None, "parsing_error": Exception|None}` ให้เข้ากับ `invoke_structured` เดิม; `ProviderInfo.structured_method="json_schema"`
- **Usage:** ใส่ `usage_metadata={"input_tokens","output_tokens","input_token_details":{"cache_read","cache_creation"}}` จาก `usage` ของ result และ `response_metadata={"total_cost_usd": …, "model": …, "duration_api_ms": …, "session_id": …}` → `extract_usage` ใช้ได้ทันที; `ModelInfo` ของ model ใช้ catalog เดิม (ราคาเท่ากับ API เพื่อให้ budget guard ยังทำงาน แม้จริงๆ จะไม่ถูกเรียกเก็บต่อ token — ระบุใน docstring)
- **Error mapping:** process ไม่พบ → `LLMNotConfigured`; exit code ≠ 0 หรือ `is_error: true` → อ่าน `subtype`/`result`: ข้อความมี "Invalid bearer token"/"authentication"/"login" → `auth`; "rate limit"/"usage limit"/"overloaded" → `rate_limit`/`overloaded` (เข้า cooldown + failover); "budget" → `billing`; อื่นๆ → `server`. timeout = `asyncio.wait_for(cfg.timeout_s)` แล้ว kill process (ทั้ง tree บน Windows: `taskkill /T` หรือ `proc.kill()` ของ asyncio subprocess + รอ)
- **Streaming:** ไม่ทำใน phase นี้ — `astream` คืนก้อนเดียว (BaseLLMClient รองรับอยู่แล้ว) ใส่ไว้ใน Follow-ups (`--output-format stream-json --include-partial-messages`)
- ห้ามรันพร้อมกันเกิน `settings.claude_code_max_concurrency` (default 2) ใช้ `asyncio.Semaphore` ระดับ module

**`config.py`:** เพิ่ม `ClientKind = Literal["anthropic","google","openai","claude_code"]`, `ProviderInfo("claude_code", "Claude (subscription via Claude Code)", "claude_code", "anthropic_api_sub_key", free=True, structured_method="json_schema", env_hint="ANTHROPIC_API_SUB_KEY (claude setup-token) + Claude Code CLI")`; `key_setting` เป็น optional จริงๆ สำหรับ provider นี้ → เพิ่ม `ProviderInfo.key_optional: bool = False` และให้ `resolve_endpoint` ไม่ raise เมื่อ `key_optional` แต่ให้ `is_provider_configured("claude_code")` = CLI หาเจอ **และ** (token ตั้งไว้ **หรือ** มี credential ของ Claude Code ในเครื่อง = `~/.claude/.credentials.json` หรือ `~/.claude.json` มี `oauthAccount`); alias `claude_sub`, `claude_subscription`, `claude_code_cli`; `AUTO_PICK_ORDER`: วางหลัง `anthropic` (ก่อน `google`); `RECOMMENDED_QUICK["claude_code"]="claude-haiku-5-5"`, `RECOMMENDED_DEEP["claude_code"]="claude-opus-5-5"`; `MODEL_SUGGESTIONS["claude_code"]=["claude-opus-5-5","claude-sonnet-5-5","claude-haiku-5-5","claude-fable-5-1","opus","sonnet","haiku"]`
**`model_catalog.py`:** เพิ่ม `claude-sonnet-5-5` (1M, 2/10, cache 0.20, sampling=False), `claude-haiku-5-5` (1M, 0.10/0.50, sampling=False) ถ้ายังไม่มี; alias `opus`/`sonnet`/`haiku` → ตัวล่าสุด
**`factory.py`:** `provider.kind == "claude_code"` → `ClaudeCodeChatModel(...)` (ไม่ import anthropic SDK); `temperature` ไม่ส่ง
**`base.py`:** `prepare()` ไม่ใส่ `cache_control` สำหรับ kind นี้ (CLI จัดการเอง)
**`settings.py`:** `anthropic_api_sub_key: SecretStr | None` (`validation_alias=AliasChoices("ANTHROPIC_API_SUB_KEY", "CLAUDE_CODE_OAUTH_TOKEN")`), `claude_code_bin: str | None = None`, `claude_code_max_concurrency: int = 2`
**`.env.example`:** เพิ่ม 3 ตัวพร้อมคอมเมนต์ (ส่วนตัวเท่านั้น, ต้อง `claude setup-token`, ห้ามใช้แทน API key ให้ผู้อื่น)
**`smoke.py --status`:** แสดง `claude_code` พร้อมเหตุผลที่ไม่ configured (ไม่พบ CLI / ไม่มี token+login)
**Settings API/UI:** catalog เดิมจะรวม provider ใหม่อัตโนมัติ; ใน `GET /settings` catalog entry เพิ่ม `note: str | null` (สำหรับ `claude_code` = "ใช้ subscription ของคุณผ่าน Claude Code CLI ที่ติดตั้งในเครื่อง — สำหรับใช้ส่วนตัวเท่านั้น") และ `configured_reason: str | null` เมื่อไม่ configured; frontend `SettingsView` แสดง note ใต้ provider list (KeyDot tooltip ใช้ `configured_reason`)

**Tests (ไม่มี network, ไม่ spawn claude จริง):** mock `asyncio.create_subprocess_exec` ด้วย fake process ที่คืน JSON ตาม fixture `tests/fixtures/llm/claude_code_result.json` (บันทึกจากผลจริงด้านบน, ลบ session id/uuid)
- สร้าง client ได้เมื่อ CLI เจอ (monkeypatch `shutil.which`); `LLMNotConfigured` เมื่อไม่เจอ; `is_provider_configured` 4 กรณี (cli×token)
- args ที่ส่ง: มี `--model`, `--tools ""`, `--no-session-persistence`, ไม่มี `--bare`, system prompt ถูกส่ง, prompt ไปทาง stdin; env ไม่มี `ANTHROPIC_API_KEY`/`CLAUDECODE` และมี `CLAUDE_CODE_OAUTH_TOKEN`; ข้อความ exception ไม่มี token
- ตอบ text ธรรมดา → `AIMessage` + `usage_metadata` ถูก (`extract_usage` ได้ค่าตรง fixture)
- bind_tools → `--json-schema` envelope และ `tool_calls` parse ถูก, tool ไม่รู้จักถูกทิ้ง
- with_structured → parsed schema; CLI ไม่มี `structured_output` → fallback parse จาก `result`
- error mapping 4 แบบ + timeout kill; `is_error` → raise ที่ `classify_error` ได้ kind ที่คาด; failover chain `claude_code → groq` ทำงาน (ใช้ `FailoverLLM` + ScriptedClient เดิม)
- live test (`-m live`, ข้ามอัตโนมัติถ้าไม่มี CLI/token): `claude-haiku-5-5` ตอบ "pong" และ structured `{"n": int}` ได้

### 16.2 Backend — โหมดกรอก position เพิ่ม (`domain/portfolio/entry.py`, `domain/compute/portfolio.py`)

**compute (pure, `Decimal`, มี provenance record, unit test ทุกตัว):**
- `avg_cost_from_pnl_pct(price, pnl_pct)` → `avg_cost = price / (1 + pnl_pct)`
- `avg_cost_from_pnl_amount(quantity, price, pnl_amount)` → `avg_cost = (quantity·price − pnl_amount) / quantity`
- `units_from_value_and_pnl_amount(value, pnl_amount, price)` → `units = value / price`, `cost_basis = value − pnl_amount`, `avg_cost = cost_basis / units`
- `avg_cost_from_cost_total(quantity, cost_total)` → `cost_total / quantity`
- ทุกตัว `ValueError` เมื่อ price/quantity ≤ 0, `pnl_pct ≤ −1`, cost_basis ≤ 0 (คือ "ขาดทุนเกินทุน") — ข้อความไทยอ่านรู้เรื่อง

**`PositionInput` (contracts §5):** เพิ่ม `pnl_amount: Decimal | None` (กำไร/ขาดทุนเป็นเงินในสกุลของ symbol, ลบ = ขาดทุน), `cost_total: Decimal | None` (ต้นทุนรวม ≥ 0), `mode: EntryMode | None` (`"qty_cost" | "qty_pnl_pct" | "qty_pnl_amount" | "value_pnl_pct" | "value_pnl_amount" | "cost_total" | "weight"`; ถ้า `None` ให้ infer จากฟิลด์ที่ส่งมา) — `avg_cost` ที่ผู้ใช้ให้มาตรงๆ ชนะทุกการคำนวณ
**`resolve_positions`:** ตารางตัดสินใจ (แถวละโหมดเดียว ฟิลด์ที่ไม่เกี่ยวต้องว่าง ไม่งั้น 422 ระบุ field):

| มี | คำนวณ | ต้องมีราคา |
|----|-------|-----------|
| quantity + avg_cost | — | ไม่ |
| quantity + pnl_pct | avg_cost | ใช่ |
| quantity + pnl_amount | avg_cost | ใช่ |
| quantity + cost_total | avg_cost | ไม่ |
| quantity อย่างเดียว | avg_cost=None (P&L ไม่รู้) | ไม่ |
| value + pnl_pct (เดิม) | units, avg_cost | ใช่ |
| value + pnl_amount | units, avg_cost | ใช่ |
| value อย่างเดียว | units, avg_cost=None | ใช่ |
| weight | declared | ไม่ |

ทุกแถวที่คำนวณ → record `user_input` + record ของ compute (เหมือน value-mode เดิม) และ `converted[]` ใส่ `mode`, `price`, `price_as_of`, `currency`, `units`, `avg_cost`, `cost_basis`, `provenance_id`
**CSV:** คอลัมน์ `pnl_amount`, `cost_total`, `mode` (optional) + template ใน docs/frontend
**Preview endpoint (contracts §11):** `POST /portfolios/{id}/positions/preview` และ `POST /portfolio/positions/preview` body `{positions: [PositionInput]}` → **ไม่บันทึก** คืน `{rows: [{index, symbol: Symbol, mode, status: "ok"|"error", quantity, avg_cost, price, price_as_of, currency, market_value, cost_basis, unrealized_pnl, unrealized_pnl_pct, provenance_id, errors: [EntryError]}], errors: [EntryError] (ระดับ book)}` — คำนวณ value/P&L ของ preview ด้วย compute เดิม (`pnl_summary` หรือฟังก์ชันที่มีอยู่) ทุกตัวเลขมี provenance id; แถวที่ไม่มีราคาคืน `price=null` + warning แทน error ถ้าโหมดนั้นไม่ต้องใช้ราคา
**Paste-from-broker (contracts §11):** `POST /portfolios/parse-holdings` body `{text, market_hint?: "TH"|"US"|null, llm?: {provider, model}}` → ใช้ tier **quick** (หรือ `llm` ที่ส่งมา) เรียก `invoke_structured` กับ schema `ParsedHoldings{rows: [{symbol, quantity?, avg_cost?, value?, pnl_pct?, pnl_amount?, cost_total?, market?, note?}], unparsed: [str]}` ด้วย prompt ไฟล์ใหม่ `domain/agents/prompts/portfolio_parse_holdings.v1.md` (version ลง provenance; กฎ: ห้ามเดาตัวเลข ให้ใส่เฉพาะที่เห็น, % ที่เป็นลบ = ขาดทุน, ตัด suffix โบรกเกอร์, ใส่ `unparsed` บรรทัดที่อ่านไม่ออก) → คืน `{rows: [PositionInput], unparsed, mode: "native"|"fallback_parsed"|"failed", provider, model}` (ไม่ resolve ราคาให้ — frontend ส่งต่อไป preview); 503 `{error: "llm_not_configured"}` เมื่อไม่มี LLM; limit `text` 20k ตัวอักษร; **ไม่มี side effect** และไม่เขียน memory
**Tests:** compute 4 ฟังก์ชัน (ค่าปกติ, ขอบ, error), `resolve_positions` ทุกแถวในตารางด้านบน (ใช้ `MarketData` fake เดิม), ฟิลด์ปนกัน → 422 ระบุ field, CSV คอลัมน์ใหม่, preview endpoint (httpx + fake data), parse-holdings ด้วย FakeLLM ที่คืน structured (ตัวอย่างข้อความ 2 แบบ: สไตล์ Dime! "NVDA 549.96 USD +14.52%" และสไตล์ Streaming "PTT 1,000 32.50 35.00 +7.69%") และกรณี LLM ไม่มี → 503

### 16.3 Frontend — เพิ่มหุ้นแบบ guided + วางข้อความ

- **`components/portfolio/AddPositionDialog.tsx`** (ใหม่): dialog 2 ขั้น
  1. เลือกหุ้น: `SymbolSearch` (ของ trade) + แสดง badge ตลาด/สกุลเงิน และ **ราคาล่าสุด** จาก preview (เรียก preview ด้วยแถวว่าง `mode: "qty_cost", quantity: "1"` ไม่ได้ — ให้ใช้ `GET /market/{symbol}/quote` ถ้ามี ไม่งั้นแสดงหลัง preview แถวจริง)
  2. "คุณเห็นอะไรในแอปโบรกเกอร์?" segmented control 6 โหมด: `จำนวน + ต้นทุนเฉลี่ย` · `จำนวน + % กำไร/ขาดทุน` · `จำนวน + กำไร/ขาดทุน (บาท)` · `มูลค่า + % กำไร/ขาดทุน` · `มูลค่า + กำไร/ขาดทุน (บาท)` · `จำนวน + ต้นทุนรวม` (+ ลิงก์ "กรอกแค่ % น้ำหนัก" ที่พาไปโหมด % ของตาราง) — ช่องกรอก 2 ช่องตามโหมด มีปุ่มสลับ **กำไร / ขาดทุน** (เขียว/แดง) แทนการพิมพ์เครื่องหมายลบ, placeholder ตามสกุลเงินของ symbol, จำช่วงโหมดล่าสุดใน `localStorage`
  - **Preview card สด** (debounce 400 ms → `POST …/positions/preview`): จำนวน · ต้นทุนเฉลี่ย · ราคาล่าสุด (as_of) · มูลค่า · กำไร/ขาดทุน (บาท, %) ด้วย `ProvValue` (hover = provenance) + บรรทัด "ระบบคำนวณจากราคาล่าสุด ค่าจะขยับตามตลาด"; error ของ backend แสดงที่ช่องนั้น (field)
  - ปุ่ม `เพิ่มอีกตัว` (สะสมในรายการด้านล่างของ dialog, แก้/ลบได้) และ `บันทึกทั้งหมด` → PUT พอร์ต (positions เดิม + ใหม่) ผ่าน `onSave` เดิมของ `HoldingsTable`/`BookDetail`; 422 → map error กลับไปที่รายการ
- **`components/portfolio/PasteHoldingsDialog.tsx`** (ใหม่) หรือแท็บ "วางข้อความ" ใน `ImportCsvDialog`: textarea (วางจากแอป/อีเมลสรุปพอร์ต) + market hint (ไทย/สหรัฐฯ/ให้เดา) → `POST /portfolios/parse-holdings` → ตารางแถวที่ AI อ่านได้ (แก้ inline ได้, แสดง `unparsed` เป็นเตือน) → preview (endpoint เดิม) → `บันทึก` (replace หรือ append เลือกได้; default append)
- **`HoldingsTable`:** ปุ่ม `+ เพิ่ม` เปิด `AddPositionDialog` (แทนการเพิ่มแถว inline), แถว draft inline ยังอยู่สำหรับแก้ของเดิม; ปุ่มสลับ qty⇄มูลค่า เปลี่ยนเป็น menu เลือกโหมด (6 โหมด) และ `holdings.ts` `DraftRow` เพิ่ม `pnlAmount`, `costTotal`, `kind` ขยายเป็น `EntryMode`; `buildPositions` ส่ง `mode` เสมอ
- `EmptyState`: ปุ่ม `เพิ่ม position` → dialog, เพิ่มปุ่ม `วางข้อความจากแอปโบรกเกอร์`
- `lib/portfolio-api.ts`: `positionsPreview`, `parseHoldings` + types; `pnpm gen:api` หลัง backend export OpenAPI
- `SettingsView`: แสดง `note`/`configured_reason` ของ catalog entry (16.1)
- Tests (vitest + testing-library, mock api): AddPositionDialog ทั้ง 6 โหมด ส่ง body ถูก + preview แสดงค่าจาก mock + สลับกำไร/ขาดทุนทำให้เครื่องหมายถูก + 422 แสดงที่ช่อง; PasteHoldingsDialog: parse → preview → save body; HoldingsTable menu โหมด; fixture `frontend/tests/fixtures` ถ้ามี pattern อยู่แล้วให้อัดใหม่

### 16.4 Docs
- `docs/contracts.md` §5/§7/§11 (แก้แล้วพร้อม phase นี้), `docs/data-sources.md` ไม่เปลี่ยน, `docs/ui-spec.md` §6 เพิ่มย่อหน้า "เพิ่มหุ้น (dialog) / วางข้อความ", `docs/README.md` เพิ่มแถว phase 16, `CLAUDE.md`: provider ครบ 11 ตัว + ตาราง phase + Commands (smoke `--provider claude_code`)

---

## Definition of Done

- [ ] backend: `ruff check`, `ruff format --check`, `mypy --strict`, `lint-imports`, `pytest` ผ่านทั้งหมด (ยกเว้น fail เดิม `test_unconfigured_providers_no_network` FRED ที่มีก่อน phase นี้ — ต้องรายงานว่ายังเป็นตัวเดิมตัวเดียว)
- [ ] `uv run python -m app.harness.llm.smoke --status` แสดง `claude_code` configured บนเครื่องนี้ และ `smoke "สวัสดี" --provider claude_code --model claude-haiku-5-5` ตอบได้; `--tier deep` เมื่อตั้ง `LLM_DEEP_PROVIDER=claude_code` ใช้ได้ใน research run จริง (มี tool call อย่างน้อย 1 ครั้ง + citation) และ trade run จริง (structured `TraderProposal` ผ่าน `mode=native`)
- [ ] `GET /api/settings` catalog มี `claude_code` (configured=true, note) และหน้า `/settings` เลือกเป็น deep tier ได้, failover ใส่ได้
- [ ] กรอกพอร์ตผ่าน dialog ทั้ง 6 โหมดบน live server (PTT.BK, AAPL) แล้ว analytics แสดง P&L ตรงกับที่ preview บอก; วางข้อความ 2 สไตล์ → AI แปลง → preview → บันทึก ได้
- [ ] frontend: `pnpm lint`, `pnpm typecheck`, `pnpm test` ผ่าน; `pnpm gen:api` รันแล้ว
- [ ] ไม่มี token หลุดใน log, provenance, exception, process argv (ตรวจด้วย test + grep)
- [ ] อัปเดตตาราง phase ใน `CLAUDE.md`

## Follow-ups (ไม่ทำใน phase นี้)
- streaming ของ `claude_code` (`stream-json`) ให้ UI เห็นตัวอักษรไหล
- อัปโหลด **ภาพหน้าจอ** พอร์ต → vision LLM แปลงเป็นแถว (ต้องมี provider ที่รองรับภาพ + ขนาดไฟล์)
- import ไฟล์ Excel/PDF statement จากโบรกเกอร์ไทย (SET Streaming, Dime!, InnovestX) แบบ parser เฉพาะเจ้า
