# Contracts — interface กลางที่ทุก phase ต้องใช้ตรงกัน

เอกสารนี้คือ **source of truth** ของ interface ที่ข้าม layer/phase ถ้า phase ไหนต้องเปลี่ยน contract ต้องแก้ไฟล์นี้ก่อนแล้วค่อยแก้โค้ด และห้ามสร้างรูปแบบซ้ำซ้อนขึ้นมาใหม่

---

## 1. Layer และทิศทางการ import

```
api/  ──►  harness/  ──►  domain/
              │              ▲
              └── implements domain/ports.py (Protocols)
```

- `domain/` import ได้เฉพาะ stdlib, pydantic, pandas, numpy, httpx และ vendor SDK ที่ใช้ดึงข้อมูล (เช่น yfinance) **ห้าม** import `langgraph`, `langchain*`, `fastapi`, `sqlalchemy` และ `app.harness`
- `domain/ports.py` ประกาศ `Protocol` ที่ domain ต้องใช้ (LLM, tool runner, event emitter) แล้ว harness เป็นฝ่าย implement (hexagonal / ports & adapters)
- `harness/` import `domain` ได้ ส่วน `api/` import ได้ทั้งสองชั้น
- ใช้ `import-linter` บังคับกฎนี้ (Phase 0)

---

## 2. Provenance (F3)

```python
# domain/models/provenance.py
class ProvenanceRecord(BaseModel):
    id: str                          # "prov_" + ulid
    kind: Literal["data", "compute", "llm_assumption", "user_input"]
    value: Any                       # scalar / small dict; ข้อมูลใหญ่เก็บ ref แทน
    unit: str | None = None          # "USD", "THB", "%", "ratio", "shares"
    source: str                      # provider id เช่น "yfinance" หรือ "compute" / "user" / model id
    tool: str | None = None          # ชื่อ tool/function ที่สร้าง record นี้
    calculation: str | None = None   # "compute.portfolio.weights@1" (module.func@version)
    params: dict[str, Any] = {}      # args ที่ใช้เรียก
    inputs: list[str] = []           # provenance ids ของ input (ต่อกันเป็น lineage graph)
    agent: str | None = None         # role ที่ใช้/เสนอค่า เช่น "portfolio_advisor"
    run_id: str | None = None
    as_of: date | datetime | None    # ข้อมูล ณ วันไหน
    fetched_at: datetime             # ดึง/คำนวณเมื่อไหร่ (UTC)
    url: str | None = None

class Provenanced(BaseModel, Generic[T]):
    value: T
    provenance_id: str
```

- `domain/data/*` และ `domain/compute/*` คืน `Provenanced[...]` หรือ `(result, list[ProvenanceRecord])` และห้ามคืนตัวเลขเปล่าออกจาก layer
- ฝั่ง domain ไม่รู้จัก store: สร้าง record แล้วส่งคืน จากนั้น harness เป็นคน persist

---

## 3. Data provider (F4)

```python
# domain/data/base.py
class Capability(StrEnum):
    PRICES = "prices"                # OHLCV (stock, .BK, forex, crypto, index)
    QUOTE = "quote"
    PROFILE = "profile"
    FUNDAMENTALS = "fundamentals"    # ratios/overview
    STATEMENTS = "statements"        # income/balance/cashflow
    NEWS = "news"                    # ticker news
    GLOBAL_NEWS = "global_news"
    MACRO = "macro"                  # FRED series
    PREDICTION_MARKETS = "prediction_markets"
    SOCIAL = "social"                # stocktwits, reddit, x, adanos
    INSIDER = "insider"
    INSTITUTIONAL = "institutional"
    FILINGS = "filings"              # filing text / sections
    ANALYST = "analyst"              # targets, recommendations
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"
    SYMBOL_SEARCH = "symbol_search"

class DataError(Exception):            # base
    retryable: bool = False
class NotConfigured(DataError): ...    # ไม่มี key → ข้ามไป provider ถัดไปโดยไม่นับเป็น failure
class RateLimited(DataError): retryable = True; retry_after: float | None
class Transient(DataError): retryable = True     # timeout, 5xx, connection
class NoData(DataError): ...           # ถามแล้วไม่มีข้อมูลจริง → failover
class Unsupported(DataError): ...      # provider ไม่รองรับ symbol/market นี้ → failover
class BadRequest(DataError): ...       # args ผิด → fail ทันที ไม่ failover

class DataProvider(Protocol):
    id: str                                   # "yfinance"
    capabilities: frozenset[Capability]
    def is_configured(self) -> bool: ...
    def supports(self, cap: Capability, symbol: Symbol | None) -> bool: ...
    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult: ...

class DataRequest(BaseModel):
    symbol: Symbol | None = None
    params: dict[str, Any] = {}               # start, end, interval, query, limit, series_id...
    as_of: date | None = None                 # look-ahead guard: ห้ามคืนข้อมูลหลังวันนี้

class DataResult(BaseModel):
    provider: str
    capability: Capability
    data: Any                                 # pydantic model ตาม capability (ดู domain/data/schemas.py)
    records: list[ProvenanceRecord]
    status: Literal["ok", "partial", "unavailable"]   # "unavailable" ≠ "empty"
    notes: list[str] = []
```

**กฎสำคัญที่ port มาจาก TradingAgents:**
- `unavailable` (fetch ล้มเหลว หรือโดน rate limit) กับ `ok + data ว่าง` (ไม่มีโพสต์/ข่าวจริง) **คนละความหมาย ห้ามปนกัน** (ดู `../TradingAgents/tradingagents/dataflows/vendors/reddit.py` docstring)
- ข้อมูลที่มีวันที่ต้องผ่าน `in_window(pub_dt, start, end)` แบบ half-open UTC (port `../TradingAgents/tradingagents/dataflows/date_window.py`)
- Router มี 2 โหมด
  - `failover`: ลอง provider ทีละตัวตามลำดับ และหยุดที่ตัวแรกที่ `ok`
  - `fanout`: เรียกทุก provider ที่ configured พร้อมกันแล้วรวมผล ใช้กับ `SOCIAL` และ `NEWS` แบบ aggregate
- Capability ถูกแบ่งเป็น core (`PRICES`, `STATEMENTS`, `NEWS`) ซึ่งล้มทั้งหมดแล้วต้อง raise ให้เห็นชัด กับ optional (`MACRO`, `PREDICTION_MARKETS`, `SOCIAL`) ซึ่งล้มแล้วให้คืน `unavailable` แทน (port `OPTIONAL_CATEGORIES` จาก `router.py`)

---

## 4. Symbol

```python
# domain/models/symbol.py
class AssetType(StrEnum): STOCK="stock"; FOREX="forex"; CRYPTO="crypto"; INDEX="index"; COMMODITY="commodity"
class Market(StrEnum): US="US"; TH="TH"; GLOBAL="GLOBAL"; OTHER="OTHER"

class Symbol(BaseModel, frozen=True):
    raw: str              # ที่ผู้ใช้พิมพ์ "PTT", "eurusd", "XAUUSD"
    canonical: str        # "PTT.BK", "EURUSD=X", "GC=F", "BTC-USD", "AAPL"
    asset_type: AssetType
    market: Market
    currency: str         # "THB", "USD" (forex = quote currency)
    display: str          # "PTT (SET)", "EUR/USD"
```

- `normalize_symbol(raw: str, hint_market: Market | None) -> Symbol` port กฎจาก `../TradingAgents/tradingagents/dataflows/symbols.py` (forex 6 ตัว → `=X`, crypto → `-USD`, XAUUSD → `GC=F`, ตัด suffix ของ broker) แล้วเพิ่มกฎไทย: `hint_market=TH` หรือพบใน SET list → `.BK`
- `safe_path_component(symbol)` ใช้ก่อนนำ symbol ไปใช้เป็น path/DB name เสมอ

---

## 5. Portfolio

```python
# domain/models/portfolio.py
class Position(BaseModel):
    symbol: Symbol
    quantity: Decimal                 # ติดลบ = short
    avg_cost: Decimal | None = None   # ต่อหน่วย ในสกุลเงินของ symbol
    target_weight: Decimal | None = None   # 0..1 ถ้าผู้ใช้กำหนด
    note: str | None = None

class Portfolio(BaseModel):
    id: str
    name: str
    base_currency: str = "THB"
    cash: Decimal | None = None       # None = ไม่ได้บอก ≠ 0
    positions: list[Position]
    updated_at: datetime

class PortfolioContext(BaseModel):
    state: Literal["positions", "flat", "not_provided"]   # 3 สถานะ ห้ามปน (port ../TradingAgents/tradingagents/portfolio.py)
    portfolio: Portfolio | None
```

**ส่วนขยายใน P12:** `Position.declared_weight` (0..1), `Portfolio.icon`, `Portfolio.color`, `Portfolio.provenance_id` (record `kind=user_input` ของ revision ล่าสุด) และ `portfolio_hash(p)` (hash ของ base/cash/positions ใช้ใน graph signature และ cache key ของ analytics)
- `PortfolioAnalytics` (`domain/portfolio/analytics.py`): ทุกตัวเลขเป็น `Provenanced[float]` (`{value, provenance_id}`) — totals, holdings (price, fx_rate, market_value, weight, unrealized/day P&L, risk_contribution, `status: ok|no_price|no_fx|declared`), weights (+`CASH`), concentration, exposure (sector/asset_type/currency/market), risk (vol, MDD, beta, VaR/CVaR 95%, correlation ≤ 30, contribution), fx, `warnings[{code,message,symbol}]`; records ไม่ถูก serialize (harness persist เอง)
- กรอก position ได้ 3 แบบต่อแถว: `quantity`(+`avg_cost`) / `value`+`pnl_pct` (แปลงเป็น units + ต้นทุนด้วย `compute.portfolio.units_from_value` จากราคาล่าสุด) / `weight` อย่างเดียว — พอร์ตเดียวห้ามปนแบบจำนวนกับแบบ %
- **(P16) โหมดกรอกเพิ่ม** — `PositionInput` เพิ่ม `pnl_amount: Decimal | None` (กำไร/ขาดทุนเป็นเงินในสกุลของ symbol, ลบ = ขาดทุน), `cost_total: Decimal | None` (ต้นทุนรวม ≥ 0) และ `mode: EntryMode | None` = `"qty_cost" | "qty_pnl_pct" | "qty_pnl_amount" | "value_pnl_pct" | "value_pnl_amount" | "cost_total" | "weight"` (None = infer จากฟิลด์ที่ส่งมา; ฟิลด์ที่ไม่อยู่ในโหมดต้องว่าง ไม่งั้น 422 ระบุ field) ส่วนที่ขาดคำนวณด้วย compute เท่านั้น: `avg_cost_from_pnl_pct(price, pnl_pct)`, `avg_cost_from_pnl_amount(quantity, price, pnl_amount)`, `units_from_value_and_pnl_amount(value, pnl_amount, price)`, `avg_cost_from_cost_total(quantity, cost_total)`; `avg_cost` ที่ผู้ใช้ให้มาตรงชนะการคำนวณ; `converted[]` ของ 422/preview/import มี `{index, symbol, mode, price, price_as_of, currency, units, avg_cost, cost_basis, provenance_id}`; CSV รับคอลัมน์ `pnl_amount`, `cost_total`, `mode` เพิ่ม; `EntryError.severity: "error" | "warning"` (warning = แถวยังใช้ได้ เช่น ดึงราคาไม่ได้ในโหมดที่ไม่ต้องใช้ราคา); ฟิลด์ที่ผู้ใช้กรอกที่ไม่อยู่ในโหมด → 422 ระบุ field นั้น; `quantity` + `weight` ในแถวเดียว → 422 ที่ `weight`
- Trading `config["portfolio"](symbol, portfolio_id)` คืน `{state, portfolio_id, equity, cash, symbol_weight, currency, fx_rate, …}` แปลงเป็นสกุลเงินของ instrument แล้ว; `TradeRunInput.portfolio_id` (None = `pf_default` หรือพอร์ตเดียวที่มี)

ผู้ใช้กรอก **% น้ำหนักเองได้** ในกรณีที่ไม่รู้จำนวนหุ้น: ให้เก็บเป็น `target_weight`/`declared_weight` และ compute จะคำนวณ `actual_weight` จากราคาเมื่อมี quantity ถ้ามีแค่ % ให้ถือ % นั้นเป็น actual (แล้ว flag `weights_declared_only=True`)

---

## 6. Tool (D3/D4)

```python
# domain/tools/base.py  (framework-neutral)
class ToolSpec(BaseModel):
    name: str                          # snake_case, unique
    description: str                   # ใช้เป็น tool description ส่งให้ LLM
    args_model: type[BaseModel]        # Pydantic → JSON schema
    read_only: bool = True             # True = รันพร้อมกันได้
    category: Literal["data", "compute", "search", "memory", "skill", "portfolio", "meta"]
    timeout_s: float = 30
    max_calls_per_run: int = 3         # D5 loop guard
    requires_approval: bool = False    # T4
    requires: frozenset[Capability] = frozenset()   # (P05) registry เปิด tool เฉพาะเมื่อมี provider configured ครบ
    limit_by: str | None = None        # (P07) arg ที่แบ่ง max_calls_per_run (skill, attempt, purpose)
    internal: bool = False             # (P10) workflow node เรียกผ่าน executor ได้ แต่ไม่เคยเสนอให้ LLM
    fn: Callable[[BaseModel, ToolContext], Awaitable[ToolOutput]]

class ToolContext(Protocol):           # (P05) harness ส่งให้ tool ตอนรัน
    run_id: str; call_id: str
    async def progress(self, message: str) -> None: ...          # → tool.progress
    def is_cancelled(self) -> bool: ...
    async def call_tools(self, calls: list[ToolCallReq]) -> list[ToolResult]: ...  # subcall ของ skill (parent_call_id)

class ToolOutput(BaseModel):
    data: Any
    summary: str                       # บรรทัดเดียวสำหรับแสดงใน UI ToolRow (เช่น "PTT.BK · 250 bars · 2025-09-24→2026-09-23")
    records: list[ProvenanceRecord] = []
    status: Literal["ok", "partial", "unavailable"] = "ok"

# harness/executor/types.py — สิ่งที่ LLM และ UI เห็นหลัง normalize
class ToolResult(BaseModel):
    call_id: str
    tool: str
    ok: bool
    status: Literal["ok", "partial", "unavailable", "error", "denied", "limited", "timeout", "cancelled"]
    summary: str
    content: str                       # text ที่ส่งเข้า LLM (ตัดตาม max_chars แล้ว)
    truncated: bool
    error: ToolError | None
    provenance_ids: list[str]
    duration_ms: int
    data: Any = Field(default=None, exclude=True)   # (P10) ToolOutput.data เต็มสำหรับ workflow node ที่ต้องคำนวณต่อ (ไม่ serialize ลง event/LLM)

class ToolError(BaseModel):
    type: str                          # "RateLimited", "BadRequest", "Timeout", "ValidationError", ...
    message: str
    retryable: bool
```

---

## 7. LLM port

```python
# domain/ports.py
class LLMMessage(BaseModel): role: Literal["system","user","assistant","tool"]; content: str; tool_calls: list[ToolCallReq] = []; tool_call_id: str | None = None
class ToolCallReq(BaseModel): id: str; name: str; args: dict[str, Any]

class LLMPort(Protocol):
    async def complete(self, messages: list[LLMMessage], *, tier: Literal["deep","quick"],
                       tools: list[ToolSpec] | None = None, purpose: str) -> LLMResponse: ...
    async def structured(self, messages: list[LLMMessage], schema: type[T], *, tier: Literal["deep","quick"],
                         purpose: str) -> StructuredResult[T]: ...
    def stream(self, messages, *, tier, purpose) -> AsyncIterator[LLMDelta]: ...

class StructuredResult(BaseModel, Generic[T]):
    value: T | None
    raw_text: str
    mode: Literal["native", "fallback_parsed", "failed"]   # T5 fallback
    usage: LLMUsage; provider: str | None; model: str | None   # (P04)
```

**ส่วนขยายใน P04** (เพิ่ม ไม่ได้เปลี่ยนของเดิม):
- `LLMMessage.provider_content: list[dict] | None` เก็บ content block ดิบของ assistant turn (เช่น thinking block ของ Claude ที่ต้องส่งกลับไปก่อน `tool_use` แบบไม่แก้ไข) harness เป็นเจ้าของ field นี้ และ domain ไม่ต้องแตะ
- `LLMResponse = {text, tool_calls, usage: LLMUsage, provider, model, duration_ms, reasoning?, provider_content?}` และมี `as_message()` สำหรับต่อท้าย conversation
- `LLMUsage = {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd}` ใช้ตัวเลขจริงจาก API
- `LLMDelta = {channel: "reasoning" | "answer", text}`
- `structured()` **ไม่ raise**: ถ้าทุก client ล้มจะคืน `mode="failed"`
- Provider ids: `anthropic`, `google` (AI Studio / Gemini), `openai` (ChatGPT), `xai` (Grok), `thaillm`, `ollama` (local), `ollama_cloud` (OLLAMA_API_KEY), `deepseek`, `groq`, `openrouter` (alias: `claude`, `gemini`, `aistudio`, `chatgpt`, `grok`)
- **(P16) `claude_code`** (alias `claude_sub`, `claude_subscription`, `claude_code_cli`; `ClientKind="claude_code"`): Claude ผ่าน **subscription ของผู้ใช้** โดย spawn Claude Code binary ที่ติดตั้งในเครื่อง (ไม่แก้ไข) แบบ headless `claude -p --output-format json --tools "" --no-session-persistence --system-prompt-file <tmp>/system.md --model … [--effort …] [--json-schema …]` ส่ง prompt ทาง stdin (system prompt เป็นไฟล์ในโฟลเดอร์ชั่วคราวใต้ `DATA_DIR/claude_code/` ที่เป็น cwd ด้วย — ไม่ติด limit ความยาว argv ของ Windows และไม่โผล่ใน process list; npm shim `claude.cmd` ถูกแทนด้วย `node cli.js` เพื่อไม่ผ่าน cmd.exe); env ของ subprocess ตัด `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/`CLAUDECODE`/`CLAUDE_CODE_*` ของ session แม่ ตั้ง `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` และใส่ `CLAUDE_CODE_OAUTH_TOKEN` จาก `ANTHROPIC_API_SUB_KEY` (`claude setup-token`) ถ้ามี; configured = พบ CLI และ (มี token หรือมี login ของ Claude Code ในเครื่อง). tool calling จำลองผ่าน `--json-schema` envelope `{text, tool_calls:[{name,args}]}` → `AIMessage.tool_calls`; structured = `--json-schema` ของ schema → `structured_output` (fallback parse จาก `result`); usage จาก `usage`/`total_cost_usd` ของ result (ต้นทุนตาม catalog ใช้กับ budget guard แม้ subscription ไม่คิดต่อ token); streaming ยังไม่รองรับ (คืนก้อนเดียว). **ใช้ส่วนตัวบนเครื่องของผู้ใช้เองเท่านั้น** — ห้ามให้ผู้ใช้อื่นใช้ผ่าน token นี้ (ตาม Claude Code legal & compliance) ถ้า deploy ให้คนอื่นต้องใช้ provider `anthropic` + API key ของเขาเอง. Settings catalog entry เพิ่ม `note: str | null`, `configured_reason: str | null`. (2026-10-14) timeout ต่อ call ของ provider นี้ใช้ `CLAUDE_CODE_TIMEOUT_S` (ค่าเริ่มต้น 300) แทน `LLM_TIMEOUT_S`; เมื่อ deep tier ของ agent เป็น `claude_code` microcompact ใช้ `CLAUDE_CODE_MC_COUNT_TRIGGER` (ค่าเริ่มต้น ปิด = ใช้ token trigger อย่างเดียว) เพราะ context 1M แต่การ clear หลัง 8 ชิ้นทำให้โมเดลดึงข้อมูลเดิมซ้ำ; provider อื่นยังเป็น D1 เดิม

---

## 8. Decision schema (T5)

```python
# domain/models/decision.py
class Verdict(StrEnum): APPROVE="APPROVE"; REVIEW="REVIEW"; REJECT="REJECT"
class Rating(StrEnum): BUY="Buy"; OVERWEIGHT="Overweight"; HOLD="Hold"; UNDERWEIGHT="Underweight"; SELL="Sell"  # port ../TradingAgents/tradingagents/agents/rating.py

class Decision(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    reason: str
    next_action: str | None = None

def route_decision(d: Decision | None, *, min_confidence: float = 0.6) -> Verdict:
    """Fail-safe: None/parse fail/confidence < min → REVIEW. ห้าม default เป็น APPROVE"""
```

schema ของแต่ละ role (TradeResearchPlan, TraderProposal, PortfolioDecision, RebalanceProposal) อยู่ใน `domain/agents/schemas.py` และทุกตัวต้องมี field `decision: Decision` ถ้า router ใช้ค่านั้น

> (P12) `RebalanceProposal{targets: [{symbol, target_weight 0..1, rationale}], new_symbols, cash_target, decision, summary}` — LLM เสนอแค่ weight; `normalize_proposal` พับ "CASH" เข้า cash_target, ตัด symbol ผิด, holding ที่ไม่อยู่ใน targets = 0, ถ้ารวม ≠ 1 ± 0.001 ให้ scale พร้อม warning; หุ้น/เงิน/metric หลังเทรดคำนวณโดย internal tool `rebalance_plan` (target weights บันทึกเป็น `kind=llm_assumption`, edit ของคน = `user_input`)

> (P10) schema ของ research_manager ชื่อ `TradeResearchPlan` เพราะชื่อ `ResearchPlan` ถูกใช้แล้วใน P07 สำหรับ scenario/role picker ของ Research Chat. `TraderProposal` เสนอแค่ *basis* ของ stop (`atr` + multiple หรือ `level`) — stop price, targets' R:R, units, notional, weight คำนวณโดย internal tool `size_trade` เสมอ (F1) และ `proposed_units` ของ LLM ถูกเขียนทับพร้อม warning `llm_size_overwritten`

---

## 9. Workflow (F5) + Graph (T1–T3)

```python
# domain/workflows/spec.py  (framework-neutral — ไม่มี LangGraph)
class NodeKind(StrEnum): AGENT="agent"; COMPUTE="compute"; GATE="gate"; APPROVAL="approval"; REPORT="report"

class NodeSpec(BaseModel):
    id: str                                  # "market_analyst"
    label: str                               # "Market Analyst" (UI)
    kind: NodeKind
    phase: str                               # "retrieve" | "analyze" | "verify" | "compute" | "synthesize" | "report"
    fn: Callable[[dict, NodeContext], Awaitable[dict]]   # คืน partial update เท่านั้น
    writes: frozenset[str]                   # state fields ที่ node นี้เขียนได้ (ตรวจตอน runtime)
    tools: list[str] = []                    # tool names ที่ node นี้ใช้ได้ (agent node)
    idempotent: bool = True                  # False = ห้ามรันซ้ำตอน resume

class RouterSpec(BaseModel):
    source: str
    fn: Callable[[dict], str]
    path_map: dict[str, str]                 # ต้องครอบคลุมทุกค่าที่ fn คืนได้ + "__fallback__" เสมอ
    returns: frozenset[str] = frozenset()    # (P09) ค่าที่ fn คืนได้ — validate_spec เช็กว่า ⊆ path_map

class WorkflowSpec(BaseModel):
    id: str; version: str
    input_model: type[BaseModel]; output_model: type[BaseModel]
    state_model: type[TypedDict]
    reducers: dict[str, Literal["replace", "append", "merge", "add"]]
    nodes: list[NodeSpec]; edges: list[tuple[str, str]]; routers: list[RouterSpec]
    entry: str
    signature_fields: list[str]              # input fields ที่เปลี่ยน graph shape → รวมใน thread_id (T3)
    def graph_view(self) -> GraphView: ...   # nodes/edges สำหรับ React Flow

class NodeContext(Protocol):                 # harness เป็นคน implement
    llm: LLMPort
    tools: ToolRunnerPort                    # เรียก tool ผ่าน executor เสมอ
    emit: EventPort
    run_id: str
    config: Mapping[str, Any]
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...   # → LangGraph interrupt()
```

---

## 10. Events (SSE) — backend ↔ frontend

ทุก event ถูกบันทึกลงตาราง `events` ก่อนแล้วค่อยส่งออก ดังนั้น UI reload หรือต่อใหม่แล้ว replay ได้ (ใช้ `Last-Event-ID` = `seq`)

```ts
// frontend/lib/events.ts  (ต้องตรงกับ backend/app/harness/events/schema.py)
type EventEnvelope<T extends EventType = EventType> = {
  seq: number            // เพิ่มทีละ 1 ต่อ run
  run_id: string
  session_id: string | null
  ts: string             // ISO UTC
  type: T
  node: string | null    // workflow node id (ถ้ามี)
  call_id: string | null
  parent_call_id: string | null   // tool ซ้อน (skill → tools) หรือ subagent
  payload: EventPayload[T]
  ephemeral: boolean     // (P05) true = llm.delta ที่ส่ง live ทีละ chunk: ไม่มี SSE id และไม่ได้บันทึก DB
}
```

**`llm.delta` coalescing (P05):** chunk ถูกส่ง live ทันทีเป็น `ephemeral: true` และบันทึกลง DB รวมเป็นก้อน (~200ms หรือก่อน event อื่น) เป็น `llm.delta` ปกติที่มี `seq` โดยข้อความของก้อนที่บันทึก = chunk ephemeral ทั้งหมดนับจากก้อนก่อนหน้า ดังนั้น UI ต้องแสดง ephemeral text เป็น "pending" และแทนที่ด้วยก้อนที่บันทึกเมื่อมาถึง (ทั้งตอน live และตอน replay) JSON schema ของ envelope export ไว้ที่ `backend/schemas/events.schema.json` (`uv run python -m app.harness.events.export`)

| type | payload | UI ที่ใช้ |
|------|---------|-----------|
| `run.started` | `{workflow, input, graph?}` | header, graph view |
| `run.finished` | `{status: "completed"\|"failed"\|"cancelled"\|"interrupted"\|"limited", error?, reason?}` | status dot |
| `node.started` | `{node, label, phase}` | graph node → ongoing |
| `node.finished` | `{node, status: "done"\|"error"\|"skipped", duration_ms, summary?}` (node ที่หยุดรอ `interrupt()` ไม่ส่ง `node.finished` — เริ่มใหม่หลัง resume) | graph node → done/error |
| `llm.started` | `{provider, model, tier, purpose: "agent"\|"final"\|"compact"\|"structured"\|"debate"}` | TurnProcess |
| `llm.delta` | `{channel: "reasoning"\|"answer", text}` | ReasoningRow / Answer |
| `llm.finished` | `{input_tokens, output_tokens, cache_read_tokens, cost_usd, duration_ms, structured_mode?}` | StatsPills |
| `tool.started` | `{tool, category, args, read_only, batch_id}` | ToolRow (running) |
| `tool.progress` | `{message}` | ToolRow shimmer text |
| `tool.finished` | `ToolResult` (ไม่มี content เต็ม มีแค่ `preview` ≤ 2k chars) | ToolRow (done/error) |
| `context.usage` | `{used_tokens, context_window, percent}` | context meter |
| `context.microcompact` | `{cleared, tokens_saved}` | CompactionItem (เล็ก) |
| `context.compaction` | `{phase: "start"\|"end"\|"failed"\|"fallback_clear", pre_tokens, post_tokens?, summary?, cleared?, kept?}` (P06: `failed` ใส่เหตุผลใน `summary`; `fallback_clear` ใส่ `cleared` = จำนวน message ที่ตัด และ `kept` = จำนวน tool result ที่เหลือ) | CompactionItem |
| `guard.limit` | `{kind: "iterations"\|"tool_calls"\|"similar_query"\|"timeout"\|"budget", detail}` | warning row |
| `warning` | `{code, message}` (partial failure เช่น provider unavailable) | warning row |
| `approval.required` | `{approval_id, kind, title, proposal, policy_results, provenance_ids}` | ApprovalPanel takeover |
| `approval.resolved` | `{approval_id, outcome: "approved"\|"rejected"\|"edited", edits?}` | ApprovalPanel |
| `answer.final` | `{markdown, citations: [{marker, provenance_id}], unverified_numbers: string[]}` | Answer + citation chips |
| `checkpoint.saved` | `{node, thread_id}` | graph view (icon) |
| `backtest.progress` | (P15) `{phase: "preload"\|"decisions"\|"simulate"\|"done", total, done, cached, failed, running, cost_usd, message?}` | แถบ progress ของหน้า Backtest |
| `backtest.cell` | (P15) `{cell_id, symbol, day, status: "pending"\|"running"\|"done"\|"failed", run_id?, rating?, side?, outcome?, cached, error?}` | CellGrid |
| `backtest.sim` | (P15) `{sim_id, final_equity (decimal string), total_return, trades}` | reload ผลลัพธ์ |

(P12) portfolio_advisor: node `diagnose` มี sub-node `diagnose.risk` / `diagnose.allocation` แบบเดียวกัน; `approval.required.kind = "rebalance_plan"` (proposal = card: rows current/target/after/action/shares/value, cash, turnover, metrics_before/after, warnings); edits = `{target_weights: {symbol: w}, cash_target?}` → คำนวณ + ตรวจ policy ใหม่

(2026-09-28) trading workflow v2: node `refresh_price` ระหว่าง `policy_gate` (ผ่าน) กับ `approval` — tool ภายใน `trade_quote` (QUOTE) ดึงราคาสด → `size_trade` ใหม่ด้วย basis เดิม (ATR stop เลื่อนตาม entry, stop/target แบบ level คงเดิม) → policy ใหม่; ถูก block → `report` (`rejected`, ไม่ถึง approval). state `live = {refreshed, quote: {price, ts, provider, provenance_ids}, snapshot_close, sizing, provenance_ids, policy}`; backtest (`analysis_date`) หรือ quote ไม่ได้ → `refreshed=false` ใช้ snapshot close + `warning live_price_unavailable`. `approval.required.proposal` เพิ่ม `price_source: "live"|"snapshot"`, `price_ts`, `snapshot_close`. ตอนตอบ approved/edited: ดึงราคาอีกครั้ง → compute + policy ใหม่ → `state.approval` มี `sizing, provenance_ids, policy, quote, entry_drift_atr`; ราคาขยับจาก entry บนการ์ดเกิน `max_entry_drift_atr` (ค่าเริ่มต้น 0.5 × ATR14) → `rejected` ("price moved … re-run"); decision log บันทึกตัวเลข ณ เวลาอนุมัติ

(2026-09-28) trading workflow v3 — horizon กำหนดข้อมูลจริง (`domain/models/horizon.py`): `intraday` = แท่ง 15m (ถือไม่กี่ชั่วโมง, ATR 1–2×, time stop default 16 แท่ง, ข่าวล่วงหน้า 24h, guard 2h), `swing` = 1h (1–5 วัน, 1.5–2.5×, 48 แท่ง, 120h, 12h), `position` = 1d (สัปดาห์–เดือน, 2–3×, 40 แท่ง, 168h, 24h). `load_context` เรียก `trade_snapshot(interval)` (ดึงแท่ง intraday ไม่ได้ → fallback 1d + `warning intraday_unavailable`) + snapshot 1d เป็น `market_snapshot.higher_tf` + `trade_event_risk` (ForexFactory, สกุลเงินของ instrument: คู่ forex = ทั้งสองขา, อื่น ๆ = สกุลเงินของมัน + USD; backtest = unavailable) → state `event_risk = {available, source, window_hours, currencies, events[{at (UTC), hours_from_now, currency, impact, title, forecast, previous, actual}], next_high_impact_hours, provenance_ids}`. `market_snapshot` เพิ่ม `interval, bar_ts, change_pct, range_high/low (252 แท่ง, intraday)`. ทุก agent เห็น horizon playbook + HIGHER TIMEFRAME + EVENT RISK. `TraderProposal` เพิ่ม `time_stop_bars` (แท่งของ timeframe; ว่าง = default ของ horizon) และ `invalidation`; `size_trade` คืน `interval, time_stop_bars, time_stop_at` (+ provenance `compute.trade.time_stop`); decision card เพิ่ม `horizon, interval, time_stop_bars, time_stop_at, invalidation`. Policy `no_high_impact_event_soon` (warn; `block_on_event_risk` → block): ข่าว high-impact ภายใน guard ของ horizon หรือปฏิทินไม่พร้อม = ไม่ผ่าน; live run เท่านั้น. Decision log record เพิ่ม `language`.

(2026-09-28) ผลการเทรด (ผู้ใช้ติ๊กเอง ระบบไม่ส่งคำสั่ง): `POST /trade/runs/{run_id}/outcome` `{outcome: tp|sl|manual_exit|time_stop|not_filled|cancelled, exit_price?, target_index? (tp), note?}` → exit price (tp = target ที่เลือก, sl = stop, manual/time stop = ต้องส่ง exit_price) + `r_multiple` จาก `compute.risk.realized_r` (provenance) → role `trade_reflector` (quick tier, port จาก TradingAgents `Reflector`) เขียนบทเรียน 2–4 ประโยคในภาษาของ run → เก็บใน decision log (`memory` ns `trade_decisions`, SQLite) เป็น `{outcome: {kind, label, exit_price, r_multiple, note, settled_at, provenance_ids}, reflection, reflection_error}`; ติ๊กซ้ำ = เขียนทับ; 404 ไม่มี decision, 422 ข้อมูลไม่พอ. `lessons(symbol)` = reflection ล่าสุด 3 อัน (เรียงตาม settled_at) ขึ้นต้นด้วย `[date horizon side → outcome, ±xR]` ป้อนให้ bull/bear และ PM. `GET /trade/decisions?symbol=&open_only=` → `{decisions: [{run_id, symbol, date, recorded_at, card, outcome, reflection, reflection_error}]}`. `GET /trade/runs/{id}` เพิ่ม `event_risk`, `trade_result`. Tool `get_economic_calendar(symbol?|currencies, hours_ahead ≤336, hours_back ≤72, min_impact)` ให้ news/macro analyst.

(2026-09-30) บทเรียน + policy ใหม่ของ trading:
- `DecisionLogPort.lessons(symbol, limit=3, others=0)` → บทเรียนล่าสุดของ symbol นี้ `limit` อัน ตามด้วยของ symbol อื่น `others` อัน (`load_context` ใช้ 3 + 2). รายการที่นับเป็นบทเรียน = มี reflection **หรือ** มี `outcome.note` (ยกเลิกแต่มี note ก็นับ). รูปแบบ `[SYMBOL date horizon side → outcome, ±xR] <reflection> Trader's note: "<note ตามตัวอักษร>"` (note ไม่ผ่านการสรุปของ reflector). ส่งให้ bull/bear, **trader** และ PM ภายใต้หัวข้อ `Lessons from past trades`
- Policy `min_risk_reward` (block): action ที่เปิดความเสี่ยงต้องมี R:R ที่ **target แรก** (`size_trade.risk_reward[0]`) **มากกว่า** `min_risk_reward` (ค่าในตัว **1.5**, ปรับขึ้นจาก 1.0 — 2026-10-02: backtest + live เจอ win rate จริง ~10-30% ซึ่งพื้นเดิม 1:1 เป็น negative expectancy; `0` = ปิด rule) ไม่มี target เลย = ไม่ผ่าน. ถูก block → trader retry ได้ 1 ครั้ง (trader กับ PM เห็นเกณฑ์นี้ใน prompt)
- (2026-10-02) `atr_multiple` ที่ trader เสนอ ถูก clamp ให้อยู่ในช่วง `HorizonSpec.atr_multiple` ของ horizon นั้นเสมอ ก่อนส่งเข้า `size_trade` (เจอจาก backtest ว่า LLM บางครั้งเสนอ atr_multiple ต่ำกว่าช่วงที่ playbook แนะนำมาก เช่น 0.5 สำหรับ intraday ที่แนะนำ 1.0-2.0 ทำให้ stop แคบเกินไปจนโดน noise ปกติ) ถูก clamp แล้วส่ง warning `atr_multiple_clamped`; prompt `trade_trader.v6` เพิ่มกฎเรื่องตำแหน่งราคาใน range (shorting ใกล้แนวรับของ range ที่ระบุไว้ หรือ longing ใกล้แนวต้าน คือ entry ที่แย่แม้เทรนด์ใหญ่จะเข้าทาง — ให้ใช้ pending order ที่ขอบตรงข้ามแทน)
- (2026-10-04) Policy ใหม่ `counter_trend_risk_reward` (block, ค่าในตัว **2.5**, `0` = ปิด rule, Settings key `min_counter_trend_risk_reward`): proposal เพิ่ม field `counter_trend: bool` คำนวณจาก `_counter_trend()` ใน `trading.py` — เทียบ daily close กับ daily SMA50/SMA200 จาก `market_snapshot.higher_tf`; short ขณะราคาปิดรายวันอยู่เหนือทั้งคู่ หรือ long ขณะต่ำกว่าทั้งคู่ = counter-trend ไม่รู้ (ไม่มี higher-timeframe data) = ไม่นับเป็น counter-trend เสมอ (fail-open ไม่ block) เมื่อ counter-trend ต้องมี R:R ที่ target แรกเกิน 2.5 แทนที่จะเป็น 1.5 ปกติ — เหตุผล: backtest 2 เดือน (XAUUSD intraday, gpt-oss:120b) พบว่าการ fade RSI/Bollinger extreme สวนเทรนด์รายวันชนะแค่ ~23% ในเดือนที่เป็นเทรนด์ ขณะที่เดือนที่ตลาดแกว่งตัว (range-bound) ชนะ ~50%
- (2026-10-04) trading workflow **v4** — ML bull/bear signal (`domain/compute/ml/`): `TradeRunInput.use_ml: bool = true` (และ `BacktestConfig.use_ml`, อยู่ใน cache key). `load_context` เรียก internal tool `trade_ml_signal(symbol, horizon, as_of?, purpose)` เฉพาะเมื่อมีโมเดลที่ผ่านการตรวจสำหรับ (symbol, horizon) — ตอนนี้มีแค่ `XAUUSD intraday` = `xau_intraday_ct25` v0.2 (LightGBM pair, เทรนใน `notebook/`, ไฟล์ + hash อยู่ใน `compute/ml/models/`) — ได้ state `ml_signal = {available, model, version, timeframe, bar_ts, p_bull, p_bear, signal: long|short|flat, tau, margin, stop, tp_r_long, tp_r_short, time_stop_bars, daily_trend, missing_features, track_record, provenance_ids}`; ไม่มีโมเดล/ปิด = `{}`, ล้มเหลว (แท่งไม่พอ, ดึงข้อมูลไม่ได้) = `{available: false, error}`. ตัวเลขทั้งหมดคำนวณโดยโค้ด (provenance `compute.ml.bull_bear@<version>`), ใช้เฉพาะแท่งที่ปิดแล้ว ≤ `as_of`; feature port จาก notebook และ test ล็อกให้ตรงกับค่าที่ notebook คำนวณ. ทุก role ที่อ่าน snapshot เห็นบล็อก `ML MODEL` (ความน่าจะเป็น, signal, track record walk-forward) เป็นหลักฐานประกอบ ไม่ใช่ gate และไม่ให้ราคา.
- (2026-10-05) trading workflow **v7** — `TradeRunInput.ml_only: bool = false` (อยู่ใน graph signature): เมื่อ ML ออก long/short `load_context` → `ml_research_plan` ข้าม analysts และ node `trader` ไม่เรียก LLM — proposal มาจาก label ของโมเดล (market entry, stop = `ml.stop_atr_multiple` × ATR, target = `tp_r_*` เป็นเท่าของระยะ stop, time stop = `time_stop_bars`). `SizeTradeArgs.target_r: list[float]` แปลง R → ราคาจาก entry/stop ใน `size_trade` (re-price ตาม live quote ได้). `trade_ml_signal` เพิ่ม `stop_atr_multiple`; `GET /trade/runs/{id}` เพิ่ม `ml_signal`. ML flat/ใช้ไม่ได้: ไม่ข้ามไปทางลัด (flat → no_trade, ใช้ไม่ได้ → pipeline LLM เต็ม)
- Policy `min_confidence` ของ trading: ค่าในตัว **0.7** และเป็น **block** (ตั้ง `block_on_low_confidence=false` → warn); ถูก block → `report` (`rejected`) ทันที **ไม่ retry trader** (`NO_RETRY_RULES`) เพราะ confidence เป็นของ PM เอง. เกณฑ์ confidence **ไม่** ถูกบอกใน prompt (กันโมเดลรายงานให้พอดีเกณฑ์). advisor ใช้ค่าในตัว 0.7 เช่นกัน (ยังเป็น warn)
- Settings `policy` เพิ่ม `block_on_low_confidence: bool` และ `min_risk_reward: float (0–10, อัตราส่วน ไม่ใช่ %)`; env `POLICY_BLOCK_ON_LOW_CONFIDENCE`, `POLICY_MIN_RISK_REWARD`
- (2026-10-05) trading workflow **v5** — v0.3 ML models (VIX/DXY/US10Y features): `REGISTRY` เพิ่ม `("XAUUSD", "swing"): "xau_swing_ct25"` (v0.3 LightGBM pair, 38 features + calendar + market + session); intraday model อัปเดตเป็น v0.3 (31 features + VIX/DXY/US10Y). `trade_ml_signal` ดึง VIX (^VIX), DXY (DX-Y.NYB), US10Y (^TNX) daily จาก yfinance เมื่อ model metadata ระบุ `v3_features: true` + `feature_groups.market` — merge backward บน server clock (ไม่มี look-ahead). `ml_signal` state เพิ่ม `confidence` (|p_bull - p_bear|), `is_nfp_day` (first Friday), `vix`, `vix_level`, `session`. บล็อก ML MODEL ใน prompt เปลี่ยนเป็น **primary input** — LLM ทำหน้าที่ filter (skip NFP/low-confidence) + adjust (SL ±0.5 ATR, TP ลดได้ถึง 1.0R สำหรับ intraday); prompt `trade_trader.v7`
- (2026-10-05) trading workflow **v6** — v0.5 ML stacked ensemble: `model.kind` เพิ่ม `"stacked_ensemble"` — specialist bull/bear (แต่ละตัวมี `scale_pos_weight`) + meta-model LightGBM 3-class (FLAT=0/LONG=1/SHORT=2) ตัดสินใจทิศทางจาก output ของ specialist + บริบทตลาด (regime, VIX, ADX, session). แก้ปัญหา bull bias ของ v0.3 (เทรด long 100%) — holdout สิงหา-กันยา 2026: Exp C (conf>0.40) ได้ +15.6R, 199 trades, 88% long / 12% short, PF 1.15. `signal_rule` เปลี่ยนเป็น `{"kind": "meta_model", "min_conf": 0.40}`; `files` เพิ่ม `meta` (3-class booster); `features` = L1 specialist features (41 ตัว); `meta_features` = input ของ meta-model (p_bull, p_bear, p_diff, p_max, p_sum + 11 context cols); `feature_groups.regime` = ["regime", "regime_trend_str", "regime_sma50_slope"] (composite จาก daily SMA20/50/200 — merge backward). `confidence` เปลี่ยนจาก |p_bull - p_bear| เป็น max_prob ของ meta-model (class probability สูงสุด). **Phase ถัดไป:** LLM event filter — LLM อ่านข่าว real-time แล้วกรอง signal ที่เสี่ยง event (FOMC, NFP, CPI) ก่อน execute
- (2026-10-05) trading workflow **v8** — Smart Money Concepts สำหรับ desk ที่ไม่มี ML นำ: `TradeRunInput.use_smc: bool = true` (อยู่ใน graph signature; `BacktestConfig.use_smc` อยู่ใน cache key). เมื่อ `ml_signal` ไม่ available (ปิด ML / ไม่มีโมเดล / โมเดลล้ม) `load_context` เรียก internal tool `trade_smc(symbol, interval, as_of?, purpose)` บนแท่งของ horizon (+ `1d` เป็น `higher_tf`) → state `smc = {available, interval, bars, bar_ts, close, atr14, swing/internal: {trend, breaks[{tag: BOS|CHoCH, bias, level, bars_ago}]}, pivots, strong_weak, premium_discount{top, bottom, equilibrium, position, zone}, order_blocks{internal, swing}[{bias, low, high, bars_ago, distance_atr}], equal_levels[{type: EQH|EQL, level, first_level, bars_ago}], fair_value_gaps[...], higher_tf?, provenance_ids}`; ML นำ = `{}`, ล้มเหลว = `{available: false, error}`. คำนวณโดย `compute/smc.py` (port LuxAlgo SMC, CC BY-NC-SA 4.0 — ใช้ที่ไม่ใช่เชิงพาณิชย์เท่านั้น; provenance `compute.smc.smart_money_concepts@v1`) จากแท่งที่ปิดแล้ว. ทุก role ที่อ่าน snapshot เห็นบล็อก `SMART MONEY CONCEPTS`; trader ใช้ prompt `trade_trader.v8` (v6 + SMC-first entries: market/limit/stop จาก structure, premium/discount, order block, FVG, liquidity) เมื่อไม่มี ML นำ ส่วนโหมด ML นำยังใช้ v7
- (2026-10-05) Policy ใหม่ `smc_structure_alignment` (block, อยู่ใน rule set ของ trading เสมอ): proposal เพิ่ม field `smc_swing_trend` (= `smc.swing.trend` ของ timeframe horizon; ไม่มี SMC = `None` → ผ่าน); action ที่เปิดความเสี่ยงต้องไปทางเดียวกับ swing structure (long ↔ bullish, short ↔ bearish) — trader retry 1 ครั้ง → REJECT. บล็อก SMC เพิ่มบรรทัด `reading` (premium/discount = ตัวกรองราคาเข้าภายในเทรนด์ ไม่ใช่สัญญาณกลับตัว) และ internal structure ของ daily.
- (2026-10-05) sizing ของ desk ที่ไม่มี ML นำ: `size_trade` รับ `min_stop_atr` (stop ที่ใกล้กว่า k × ATR ถูกขยายเป็น k × ATR, provenance `compute.trade.stop_floor`, size ลดตาม) และ `first_target_r` (target แรก = entry ± r × ระยะ stop หลังขยาย, provenance `compute.trade.first_target`; `target_levels` ของ trader ที่อยู่เลย target แรกเป็น target ถัดไป ที่อยู่ใน target แรกถูกตัดพร้อม note). workflow ส่งค่าจาก config `min_stop_atr` (ค่าในตัว **2.0**) และ `first_target_r` (ค่าในตัว **1.6**; `0` = ปิด) เฉพาะเมื่อ `_is_ml_led` เป็นเท็จ — โหมด ML ใช้ label ของโมเดลเดิม; Settings `policy.min_stop_atr` / `policy.first_target_r` และ `/api/settings` `policy_builtin` เพิ่มสองคีย์นี้; `_limits_text` บอก trader ว่าระบบตั้ง target แรกและขยาย stop. ผลข้างเคียง: `counter_trend_risk_reward` (2.5) จะบล็อกไม้สวนเทรนด์ daily เสมอเมื่อ `first_target_r` = 1.6
- (2026-10-05) indicators: `compute.indicators.macd@v2` — signal line = **SMA9 ของ MACD** (ChrisMoody `CM_MacD_Ult_MTF`; `signal_ma=\"ema\"` = แบบเดิม; ML features ยังใช้ EMA ของตัวเอง ไม่เปลี่ยน) → ค่า `macd_signal`/`macd_hist` ใน snapshot, `compute_indicators`, `GET /market/{s}/indicators` เปลี่ยนตาม; snapshot เพิ่ม `indicators.rsi_ma14` (SMA14 ของ RSI) และ `signals = {macd_hist_state: rising_above|falling_above|falling_below|rising_below, macd_cross: bullish|bearish|null, rsi_divergence: {bullish: bars_ago|null, bearish: bars_ago|null}}` (TradingView RSI regular divergence, pivot 5/5, 5–60 แท่ง; `compute.indicators.rsi_divergence@v1`); `market_snapshot` state เก็บ `signals` และ `_snapshot_text` พิมพ์เป็นข้อความให้ทุก role
- (2026-10-05) trading workflow **v9** — SMC setup gate (desk ที่ไม่มี ML นำ): `compute.smc.smart_money_concepts@v2` เพิ่ม `sweeps` (equal high/low ที่ถูกปิดทะลุ = `continuation`, EQH→long / EQL→short; แทงไส้แล้วปิดกลับ = `reversal`; แต่ละระดับใช้ครั้งเดียว; เก็บ `swing_trend` ณ แท่งนั้น, `bar_high`/`bar_low`, `bars_ago` ≤ 50) และ `compute/smc_setup.py` `sweep_continuation_setup` (`compute.smc_setup.sweep_continuation@v1`): active เมื่อมี continuation sweep ภายใน 16 แท่ง ไปทางเดียวกับ swing trend ณ ตอนนั้นและตอนนี้, momentum ณ แท่ง sweep ผ่าน (MACD hist rising/falling หรือ cross **และ** RSI เหนือ/ใต้ RSI-MA14 หรือ divergence ≤ 10 แท่ง), ยังไม่มีแท่งปิดเลย high/low ของแท่ง sweep → `{active, side, level, bars_ago, structure_stop, swing_trend, reason, candidates[{checks, momentum}]}`; tool `trade_smc` คืน `setup` ด้วย. state `smc.setup_gate` = config `require_smc_setup` (ค่าในตัว **true**; Settings `policy.require_smc_setup`); เมื่อ gate เปิด: trader prompt **v9**, บล็อก SMC เพิ่มบรรทัด `SMC SETUP`, policy ใหม่ `smc_setup_required` (block; proposal field `smc_setup_side` = None ไม่มี gate / `"none"` / `long` / `short`), `counter_trend` = False และ `_limits_text(ctx, state)` ไม่แสดงกฎ counter-trend (setup ใช้ swing trend แทน). ที่มา: rule study GC=F 1h 730 วัน — B + trend + momentum เป็นแบบเดียวที่บวก (+0.23R/ไม้, 36 ไม้, บวกเฉพาะ 2026)
- (2026-10-05) snapshot `signals.rsi_divergence` แสดงเฉพาะ divergence ที่ pivot อายุ ≤ `DIV_REPORT_MAX_BARS` (20) แท่ง (เดิมรายงานอันล่าสุดไม่ว่าเก่าแค่ไหน); เพิ่ม `indicators.divergence_events(rsi, highs, lows)` → `[{kind, pivots, known_at}]` สำหรับใช้แบบ point-in-time
- (2026-10-05) `smart_money_concepts(..., on_bar=callable)` เรียก hook ทุกแท่งด้วย state ภายใน (อ่านอย่างเดียว) และ `smc_feature_series(bars)` (`compute.smc.smc_feature_series@v1`) คืน feature SMC แบบ point-in-time ของทุกแท่งใน pass เดียว (trend ±1/0, ตำแหน่งใน swing range, ระยะ OB/FVG ใกล้สุดเป็น ATR, แท่งนับจาก break/sweep ล่าสุด cap 200) — ใช้โดย `notebook/xau_ml/smc_ind.py`
- (2026-10-05) Policy ใหม่ `smc_structure_alignment` (block, อยู่ใน rule set ของ trading เสมอ): proposal เพิ่ม field `smc_swing_trend` (= `smc.swing.trend` ของ timeframe horizon; ไม่มี SMC = `None` → ผ่าน); action ที่เปิดความเสี่ยงต้องไปทางเดียวกับ swing structure (long ↔ bullish, short ↔ bearish) — trader retry 1 ครั้ง → REJECT. บล็อก SMC เพิ่มบรรทัด `reading` (premium/discount = ตัวกรองราคาเข้าภายในเทรนด์ ไม่ใช่สัญญาณกลับตัว) และ internal structure ของ daily; `trade_trader.v8` ให้ target แรกเป็นระดับที่ใกล้ที่สุดที่ผ่าน R:R floor

(2026-09-30) คำสั่งรอเข้า (pending order: buy/sell limit, buy/sell stop):
- `TraderProposal` เพิ่ม `order_type: "market"|"limit"|"stop"` (ค่าเริ่มต้น `market`), `order_price: float|null` (ราคาที่รอเข้า; ต้องมีเมื่อไม่ใช่ market) และ `order_valid_bars: int|null` (คำสั่งค้างได้กี่แท่งของ timeframe; ว่าง = `time_stop_bars` default ของ horizon). prompt `trade_trader.v4`
- `size_trade` รับ `order_type, order_price, order_valid_bars, market_price` (ราคาตลาดตอนนี้: live quote หรือ snapshot close) และ `max_order_distance_atr` (ค่าในตัว **2.0**, config `max_order_distance_atr`). คำสั่งรอเข้า: **entry = `order_price`** (stop ATR, R:R, size, risk คิดจากราคานี้) และตรวจแบบ deterministic — ทิศ: buy limit < ตลาด, sell limit > ตลาด, buy stop > ตลาด, sell stop < ตลาด; ระยะ `|order_price − market| ≤ max_order_distance_atr × ATR14`; ไม่มี ATR หรือราคาตลาด = ไม่ผ่าน. ผลลัพธ์เพิ่ม `order_type, order` (`market|buy_limit|sell_limit|buy_stop|sell_stop`), `market_price, order_distance_atr, order_error (null = ผ่าน), order_valid_bars, order_expires_at, time_stop_from ("entry"|"fill")`; คำสั่งรอเข้าให้ `time_stop_at = null` (time stop นับจากตอนได้เข้า) + provenance `compute.trade.order`
- Policy `valid_pending_order` (block): `order_error` ไม่ว่าง = ไม่ผ่าน (market ผ่านเสมอ); trader retry ได้ 1 ครั้ง
- `refresh_price`/approval: ราคาสดแทน `market_price` เท่านั้น entry ยังเป็น `order_price` แล้วตรวจทิศ/ระยะใหม่ (ราคาวิ่งเลยจุดแล้ว = ไม่ผ่าน). การเช็ค `max_entry_drift_atr` ใช้กับ market order เท่านั้น. approval `edited` แก้ `order_price` ได้ (คง `order_type`)
- decision card เพิ่ม `order_type, order, market_price, order_distance_atr, order_valid_bars, order_expires_at, time_stop_from`; ผลการเทรด `not_filled` ใช้กับคำสั่งที่ไม่ได้เข้า

(P11) trading: analyst แต่ละตัวใน node `analysts` เป็น **sub-node** `analyst.<name>` — มี `node.started`/`node.finished` ของตัวเอง และ event ของ llm/tool ของ analyst นั้นติด `node="analyst.<name>"` (ไม่มี checkpoint แยก; checkpoint ยังเป็นของ `analysts`)

---

## 11. HTTP API

Base: `/api` ทุก response เป็น JSON ยกเว้น SSE และ error ใช้รูปแบบ `{error: {type, message}}`

| Method | Path | ใช้ทำอะไร | Phase |
|--------|------|-----------|-------|
| GET | `/health` | health | 0 |
| GET | `/config/status` | provider/LLM ไหน configured (boolean เท่านั้น ห้ามส่ง key): `{data: {...}, llm: {deep, quick, failover: {deep, quick}, providers}}` | 3, 4 |
| POST | `/dev/echo-run?symbol=` | demo workflow `echo_tools` (เฉพาะ `APP_ENV=dev`) | 5 |
| GET | `/symbols/search?q=&market=` | ค้นหา symbol → `{query, normalized, matches}`; (P11) `normalized` = query ที่ normalize แบบ offline (null ถ้าไม่ถูกต้อง) และ provider ล้ม → `matches: []` | 2, 11 |
| GET | `/market/{symbol}/ohlcv?interval=1d&range=1y&as_of=` | กราฟราคา + provenance id; (P11) + `currency`, `asset_type`, `as_of` ตัดแท่งหลังวันนั้น (backtest); `interval` ∈ `1m,5m,15m,1h,1d,1wk` (400 ถ้าไม่รู้จัก) | 2, 11 |
| GET | `/market/{symbol}/indicators?set=sma20,sma50,sma200,bb20,rsi14,macd&interval=1d&range=&as_of=` | (P11) series ของ indicator คำนวณด้วย `domain/compute/indicators` (ดึงประวัติยาวกว่าช่วงเพื่อ warm-up แล้วตัดให้เท่าช่วง; intraday ใช้ warm-up `1m→5d, 5m/15m→1mo, 1h→3mo` และตัดตาม timestamp) → `{symbol, ts, series: {sma20, bb_upper, bb_mid, bb_lower, rsi14, macd, macd_signal, macd_hist, …}, provenance: {set: pid}, price_provenance_id, provider}`; `range` เพิ่ม `1d`, `5d`; 400 set/range/interval ไม่รู้จัก. PriceChart poll สอง endpoint นี้เมื่อไม่ได้ดูย้อนหลัง (1m ทุก 15s, 5m 30s, 15m/1h 60s, 1D 5 นาที) | 11 |
| GET | `/market/{symbol}/quote` | ราคาล่าสุด | 2 |
| GET | `/skills` | รายการ scenario/skill | 6 |
| POST | `/sessions` `{kind: "research"\|"portfolio", title?}` | สร้าง session | 6 |
| GET | `/sessions?kind=` / `/sessions/{id}` | list / detail (messages + runs) | 6 |
| DELETE | `/sessions/{id}` | ลบ session พร้อม messages, runs, events, provenance, approvals และไฟล์ scratchpad ของ run นั้น (memory ไม่ลบ) → `{deleted, runs}`; 409 ถ้ายังมี run ทำงานอยู่ | 8 |
| POST | `/sessions/{id}/messages` `{text, skill?, llm?: {provider, model}, portfolio_ids?}` (P12: `portfolio_ids` = toggle "ใช้ข้อมูลพอร์ต" → preload tool `get_portfolio_analytics` ต่อพอร์ต และ tool ถูกซ่อนเมื่อว่าง; session `kind=portfolio` ใช้พอร์ตของ session/ทุกพอร์ตอัตโนมัติ, 400 ถ้ายังไม่มีพอร์ต) | เริ่ม run → `{run_id}`; `llm` = ใช้โมเดลนี้เป็น tier deep (agent + คำตอบสุดท้าย) แบบไม่มี failover ส่วน tier quick (plan, compaction) ใช้ตาม `.env` (400 ถ้า provider ยังไม่มี key พร้อมบอก env ที่ต้องตั้ง) | 6, 8 |
| GET | `/llm/providers` | ข้อมูลตัวเลือกโมเดล: `{default, providers: [{id, label, configured, env_hint, free, recommended, models}]}` (อ่าน key จาก `.env` ใหม่ทุกครั้ง ไม่ส่ง key ออก) | 8 |
| GET | `/runs/{id}/events` (SSE, `Last-Event-ID`) | stream + replay | 4 |
| POST | `/runs/{id}/cancel` | ยกเลิก | 4 |
| GET | `/runs/{id}` | สถานะ + สรุป; (P09) + `current_node`, `pending_approval`, `resumable` | 4, 9 |
| GET | `/runs/{id}/scratchpad` | JSONL entries (paginate) | 4 |
| GET | `/runs/{id}/provenance` / `/provenance/{pid}` (+ `?lineage=1`) | provenance | 4 |
| GET | `/workflows/{id}/graph` | nodes/edges สำหรับ React Flow | 8 |
| POST | `/trade/runs` `TradeRunInput` (+ `llm?`) | เริ่ม trading workflow → `{run_id, thread_id}`; 400 symbol ไม่ถูกต้อง, 409 ถ้ามี run ของ symbol/วัน/graph เดียวกันยังเปิดอยู่ | 10 |
| GET | `/trade/runs?symbol=` / `/trade/runs/{id}` | history `{run_id, symbol, date, rating, verdict, outcome, status, created_at}` / `{run, input, graph_view, nodes, instrument, market_snapshot, reports, invest_debate, research_plan, trade_proposal, risk_debate, decision, policy, live, approval, outcome, report_md}` (`live` เพิ่ม 2026-09-28; P11 เพิ่ม `market_snapshot`, `invest_debate`, `risk_debate`; `nodes` รวม sub-node `analyst.*`) | 10, 11 |
| POST | `/runs/{id}/resume` | resume จาก checkpoint (status `interrupted`/`failed`) → `{run_id, resumed_from_node}`; 409 ถ้ากำลังรอ approval | 9 |
| GET | `/runs/{id}/state` | graph state ล่าสุดจาก checkpoint `{values, next, thread_id}` | 9 |
| POST | `/approvals/{approval_id}` `{outcome, edits?, comment?}` | ตอบ approval แล้ว resume run; 409 ถ้าตอบไปแล้ว. trading `edits` (P11) = **basis** เท่านั้น: `final_size_pct` (0–1 ของขนาดที่ PM เสนอ), `stop_level` (ราคา), `target_levels` (≤ 5 ราคา) → compute คำนวณตัวเลขใหม่ + ตรวจ policy ใหม่; ถูก block หรือ edit ผิดรูปแบบ → outcome `rejected` พร้อม comment | 9, 11 |
| POST | `/dev/graph-demo` | demo A→B→C สำหรับทดสอบ kill/resume (เฉพาะ `APP_ENV=dev`) | 9 |
| GET/PUT | `/portfolio` | อ่าน/บันทึกพอร์ต `pf_default` → `{portfolio, state, portfolio_hash}`; GET 404 = ยังไม่เคยบันทึก (not_provided ≠ พอร์ตว่าง); PUT = replace ทั้งพอร์ต `{name?, base_currency?, icon?, color?, cash, positions: [PositionInput]}` → 422 `{error, errors: [{index, symbol, field, message}], converted}` | 12 |
| POST | `/portfolio/import?commit=0\|1` (body `text/csv`) | preview `{rows, positions, converted, errors}` / commit (แทนที่ positions, cash คงเดิม, revision `import`) | 12 |
| GET | `/portfolio/analytics?fresh=` | `PortfolioAnalytics` (cache 60s ตาม portfolio_hash) | 12 |
| POST | `/portfolio/advise` `{goal?, risk_profile, constraints?, language?, llm?}` | เริ่ม `portfolio_advisor` → `{run_id, thread_id}`; 404 ไม่มีพอร์ต, 400 ไม่มี position, 409 run ของพอร์ต+settings เดียวกันยังเปิดอยู่ | 12 |
| GET | `/portfolio/plans` / POST `/portfolio/plans/{id}/apply?dry_run=&force=` | แผนที่ approve แล้ว / apply → revision `apply_plan` + diff; 409 ถ้า apply แล้ว หรือพอร์ตเปลี่ยนหลังทำแผน (เว้น `force=1`) | 12 |
| GET | `/portfolio/revisions` | ประวัติการแก้พอร์ต (snapshot + provenance id) | 12 |
| GET/POST | `/portfolios` | (P12 §12.7) รายการพอร์ต / สร้างพอร์ตใหม่ (`name` จำเป็น) | 12 |
| GET/PUT/PATCH/DELETE | `/portfolios/{id}` (+ `/analytics`, `/import`, `/advise`, `/plans`, `/plans/{pid}/apply`, `/revisions`, `/snapshots`) | เหมือน `/portfolio*` แต่ระบุพอร์ต; PATCH แก้แค่ name/icon/color | 12 |
| GET | `/portfolios/summary?currency=THB&market=ALL\|TH\|US&alt_currency=` | มุมมองรวมแบบ Dime!: totals, `portfolios[]` (value, P&L, weight), `assets[]` (symbol เดียวกันรวมข้ามพอร์ต), `fx`, `provenance_id`, `alt` (สกุลเงินที่สอง) | 12 |
| GET / POST | `/portfolios/reports` / `/portfolios/reports/run` `{portfolio_ids?}` | รายงานรายสัปดาห์ (ศุกร์ 03:00 Asia/Bangkok, catch-up ตอน start): schedule + snapshots พร้อม run_id/session_id / ปุ่ม "รันตอนนี้" | 12 |
| POST | `/portfolios/{id}/positions/preview`, `/portfolio/positions/preview` `{positions: [PositionInput]}` | (P16) **ไม่บันทึก** — resolve ทุกโหมดกรอก + ราคาล่าสุด → `{rows: [{index, symbol, mode, status: "ok"\|"error", quantity, avg_cost, price, price_as_of, currency, market_value, cost_basis, unrealized_pnl, unrealized_pnl_pct, provenance_id, errors: [EntryError]}], errors: [EntryError]}` ทุกตัวเลขมี provenance id (แถวมี `provenance: {quantity, avg_cost, price, market_value, cost_basis, unrealized_pnl, unrealized_pnl_pct, weight}` → id ต่อฟิลด์, record ถูกเก็บจริงจึง hover ได้) + `input_symbol`, `weight` (แถวโหมด %); `symbol=null` เมื่อ resolve ไม่ได้; โหมดที่ไม่ต้องใช้ราคาแต่ดึงราคาไม่ได้ → `price=null` + `errors[]` ที่ `severity="warning", field="price"` และ `status` ยังเป็น `ok`; `pf_default` ใช้ได้ก่อนบันทึกครั้งแรก, id อื่นที่ไม่มี → 404 | 16 |
| POST | `/portfolios/parse-holdings` `{text (≤ 20k), market_hint?: "TH"\|"US"\|null, llm?: {provider, model}}` | (P16) วางข้อความจากแอปโบรกเกอร์ → tier quick (หรือ `llm`) + `invoke_structured` schema `ParsedHoldings{rows:[{symbol, quantity?, avg_cost?, value?, pnl_pct?, pnl_amount?, cost_total?, market?, note?}], unparsed:[str]}` prompt `portfolio_parse_holdings.v1.md` → `{rows: [PositionInput] (mode ใส่ให้แล้ว เว้นแถวที่ไม่มีตัวเลข), unparsed, notes: [EntryError severity=warning], mode: "native"\|"fallback_parsed"\|"failed", provider, model, prompt_version, usage: LLMUsage}` ไม่ resolve ราคา ไม่มี side effect; parse ไม่ได้ → 200 `mode="failed", rows=[]`; 503 `{error: {type: "llm_not_configured", message}}`; 400 เมื่อ `llm` ที่เลือกไม่มี key; 422 เมื่อ text ยาวเกิน | 16 |
| GET | `/portfolios/{id}/advisor-runs?limit=10` | (P13) run `portfolio_advisor` ล่าสุดของพอร์ต (ใหม่สุดก่อน) `{run_id, status, active, started_at, finished_at, risk_profile, outcome, plan_id, report_md}` — หน้าเว็บใช้หา run ที่รออนุมัติหลัง reload | 13 |
| GET | `/settings` | (P13) `{values, defaults, policy_builtin, overrides, catalog}`: ค่าที่มีผลตอนนี้ / ค่าจาก env + routing.yaml / ค่าในตัวของ policy / override ที่เก็บไว้ต่อ section / รายการ LLM provider (configured, env_hint, models) และ data provider ต่อ capability (mode, configured, tier) — **ไม่มี API key ใน response** | 13 |
| PUT | `/settings` `{llm?: {deep?, quick?, failover?}, data?: {order: {capability: [provider]}}, limits?: {...}, policy?: {...}}` | แทนที่เฉพาะ section ที่ส่งมา (`{}` = กลับค่าเริ่มต้น), เก็บในตาราง `settings` แล้วมีผลกับ run ถัดไป (Settings object, ลำดับของ data router, policy config ของ trading/advisor); ฟิลด์ที่ไม่รู้จัก (เช่น key) → 422; provider ที่ serve capability นั้นไม่ได้ / ซ้ำ / ว่าง → 422. limits: `max_iterations, max_tool_calls_total, max_calls_per_tool, similarity_threshold, tool_timeout_s, run_wall_clock_s, max_cost_usd, mc_count_trigger, mc_keep_recent, mc_token_trigger, autocompact_buffer_tokens`; policy (สัดส่วน 0–1): `max_position_weight, max_sector_weight, max_risk_per_trade, min_confidence, require_stop_loss, max_turnover` + (2026-09-30) `block_on_low_confidence` (bool), `min_risk_reward` (อัตราส่วน) (advisor: แทนค่าของ risk profile, constraint ของ run ยังชนะ) | 13 |
| POST | `/backtests/estimate` `BacktestConfig` | (P15) `{cells, cached, to_run, approximate, limit: 400, per_run: {cost_usd, llm_calls, minutes}, based_on_runs, est_cost_usd, est_llm_calls, est_minutes}` — เฉลี่ยจาก trade run 30 ตัวล่าสุดที่ใช้ LLM เดียวกัน (ไม่มี = `null`) | 15 |
| POST | `/backtests` `BacktestConfig` `{name?, symbols ≤10, start_date (อดีต), end_date, horizon, cadence? (วันทำการ; ว่าง = intraday 1 / swing 3 / position 5), analysts?, debate_rounds, risk_rounds, language, llm?, concurrency 1–4, budget_usd?, baseline, sim: SimConfig}` | → `{backtest_id}` = run id ของ workflow `backtest` (wall clock 24h); events/cancel/resume ใช้ `/runs/{id}/…` เดิม; > 400 cell → 400 | 15 |
| GET | `/backtests`, `/backtests/{id}` | รายการ (brief + final_equity/total_return/cost) / รายละเอียด `{…brief, run, config, output, running, cells[], sims[] (brief), latest_sim (+result)}` | 15 |
| POST | `/backtests/{id}/simulate` `SimConfig` | จำลองใหม่จากการตัดสินใจที่เก็บไว้ (ไม่เรียก LLM) → `{sim_id, created_at, config, metrics, result: {trades, skipped, equity_curve, benchmark, intervals, baseline?}}`; ไม่มี FX ของสกุลเงินฐาน → 422 | 15 |
| GET / DELETE | `/backtests/{id}/sims/{sim_id}` / `/backtests/{id}` | ผล sim หนึ่งตัว / ลบ backtest + cells + sims (trading run ของแต่ละ cell ยังอยู่เป็น cache); กำลังรัน → 409 | 15 |

(P15) `SimConfig` (เงินเป็น decimal string, สัดส่วน 0–1): `initial_capital=100000, base_currency=THB, risk_pct=0.01 (≤0.05), max_position_weight=0.20 (null = ไม่จำกัด), max_open_positions=5, leverage={forex: 10}, compounding=true, shortable=["forex"], commission_pct=0 (ต่อข้าง), min_commission=0, slippage_bps=5, intrabar_rule=stop_first|target_first, scale_out=first_target|equal_split, default_order_valid_bars=5`. `TradeRunInput.backtest_id` (ภายใน: `POST /trade/runs` ที่ส่งมา → 400) = auto-approval (APPROVE → approved, อื่น ๆ → rejected), ไม่อ่าน/เขียน decision log จริง, อยู่ใน `signature_fields`. trading run ที่มี `analysis_date` ในอดีตรันหลัง `PointInTimeRouter` เสมอ (PRICES/QUOTE ตัดที่วันนั้น, capability อื่น clamp `as_of`)

---

## 12. Storage

- `DATA_DIR` (default `backend/.data/`, gitignored)
  - `app.db` (SQLite ผ่าน SQLAlchemy 2.0 + Alembic): `sessions`, `messages`, `runs`, `events`, `provenance`, `portfolios`, `positions`, `portfolio_revisions`, `plans`, `portfolio_snapshots` (P12, migration 0003; เงินเก็บเป็น decimal string), `approvals`, `memory`, `settings`, `backtest_cells` + `backtest_sims` (P15, migration 0005: ตัว backtest เป็นแถวใน `runs` workflow `backtest`; cell มี `cache_key` = hash(symbol, วัน, horizon, analysts, rounds, ภาษา, LLM, prompt `trade_*.md`) ให้ backtest อื่นใช้ผลซ้ำ)
  - `scratchpad/{run_id}.jsonl` เป็น append-only (D2)
  - `checkpoints/{SAFE_SYMBOL}.db` สำหรับ LangGraph SqliteSaver แยกตาม symbol (T3); research/portfolio ใช้ `checkpoints/_workflow_{id}.db`
  - `cache/` สำหรับ HTTP cache ของ data provider
- ทุก ID ใช้ ULID พร้อม prefix: `ses_`, `run_`, `prov_`, `call_`, `apr_`, `pf_`, `plan_`, `rev_`, `snap_`, `cell_`, `sim_` (พอร์ตหลักใช้ id คงที่ `pf_default`)
