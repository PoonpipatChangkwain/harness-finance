# Phase 03 — Tier-B/C providers + failover ครบชุด (F4)

## เป้าหมาย
เพิ่ม provider ที่ต้องใช้ key ทั้งหมดใน [data-sources.md](../data-sources.md) ให้ทำงานใต้ router เดิม โดยถ้าไม่มี key ต้องข้ามได้เงียบๆ และถ้ามี key ต้องได้ข้อมูลที่ดีกว่าแบบอัตโนมัติ

## ต้องเสร็จก่อน
P02

## อ่านก่อนเริ่ม
- `docs/data-sources.md` §1 (ไฟล์ต้นทางของแต่ละ provider), §2 (ลำดับ), §5 (กฎการ port)
- `../TradingAgents/tradingagents/dataflows/vendors/fred.py`, `alpha_vantage/*.py`
- `../FinRobot/finrobot/data_source/finnhub_utils.py`, `fmp_utils.py`, `reddit_utils.py`, `sec_utils.py`
- `../FinRobot/finrobot_equity/core/src/modules/market_data_api.py`, `retail_sentiment_client.py`
- `../dexter/src/tools/finance/*.ts` (ทุกไฟล์ โดยเฉพาะ `api.ts`, `formatters.ts`, `utils.ts`)
- `../dexter/src/tools/search/{exa,perplexity,tavily,langsearch,web-search,x-search}.ts`
- `../TradingAgents/tradingagents/agents/post_screen.py`

## Tasks

### 3.1 Providers (`app/domain/data/providers/`)

| ไฟล์ | Env | Capabilities | รายละเอียดที่ต้อง port |
|------|-----|--------------|----------------------|
| `fred_provider.py` | `FRED_API_KEY` | MACRO | series presets: `FEDFUNDS, DGS2, DGS10, T10Y2Y, CPIAUCSL, CPILFESL, UNRATE, PAYEMS, GDPC1, VIXCLS, DTWEXBGS`, clamp realtime ตาม America/Chicago, คืน `MacroSeries` พร้อม % change |
| `alpha_vantage_provider.py` | `ALPHA_VANTAGE_API_KEY` | PRICES (TIME_SERIES_DAILY_ADJUSTED, FX_DAILY), FUNDAMENTALS (OVERVIEW), STATEMENTS, NEWS (NEWS_SENTIMENT), INSIDER | ตรวจข้อความ "rate limit"/"Note" ใน body → `RateLimited` และใช้ token bucket 5 req/min |
| `finnhub_provider.py` | `FINNHUB_API_KEY` | PROFILE, NEWS (company-news), FUNDAMENTALS (basic financials), QUOTE | 429 → RateLimited |
| `fmp_provider.py` | `FMP_API_KEY` | ANALYST (price target consensus), FUNDAMENTALS (key metrics, ratios, EV, market cap history), peers | endpoint ตาม fmp_utils.py และ market_data_api.py |
| `financial_datasets_provider.py` | `FINANCIAL_DATASETS_API_KEY` | PRICES, STATEMENTS, FUNDAMENTALS (key ratios + historical), FILINGS (10-K/10-Q/8-K items), INSIDER, INSTITUTIONAL, ANALYST (earnings), segments, screener, crypto | port ทุก endpoint ใน `dexter/src/tools/finance/` และ `stripFieldsDeep` เพื่อตัด field ซ้ำซ้อน, header `X-API-KEY` |
| `x_provider.py` | `X_BEARER_TOKEN` | SOCIAL | recent search `$TICKER OR #TICKER -is:retweet lang:en OR lang:th`, pagination รอ 350ms, 429 รอ reset ไม่เกิน 60s สูงสุด 3 ครั้ง, `public_metrics` |
| `adanos_provider.py` | `ADANOS_API_KEY` | SOCIAL (aggregate) | port `PLATFORM_SPECS` และคืนเป็น `SocialPost` แบบสรุป (platform-level sentiment + mentions) โดยใส่ `kind="aggregate"` |
| `reddit_api_provider.py` | `REDDIT_CLIENT_ID/SECRET` | SOCIAL | PRAW ผ่าน `to_thread`, subreddits: stocks, investing, wallstreetbets, SecurityAnalysis, Forex, ThaiStock (ถ้ามี) ส่วน router ใช้ตัวนี้แทน reddit_rss เมื่อ configured |
| `sec_api_provider.py` | `SEC_API_KEY` | FILINGS (10-K section) | ExtractorApi ผ่าน REST (ไม่ใช้ SDK) |
| `search/exa.py`, `perplexity.py`, `tavily.py`, `langsearch.py` | ตาม env | WEB_SEARCH | ใช้ schema `WebSearchHit(title, url, snippet, published_at?, source)` เดียวกัน, ลำดับ failover ตาม Dexter และ Perplexity คืน answer + citations (เก็บ citations เป็น hits) |

### 3.2 Post-processor: Jev screening
- `app/domain/data/postprocess/jev_screen.py` port `post_screen.py`: ถามสองคำถามต่อโพสต์ (about instrument? stance?), threshold `_OFF_TOPIC_BELOW=0.3` และ `_STANCE_CONFIDENCE=0.5`, workers 16, retry 429/529
- router เรียกหลัง fanout ของ SOCIAL เมื่อมี `TYPESAFE_API_KEY` และคืน `stance_counts` + note ถ้าล้มให้คงโพสต์เดิมไว้และใส่ note "screening unavailable"

### 3.3 Rate limiting กลาง
- `app/domain/data/ratelimit.py`: token bucket ต่อ provider (config ใน routing.yaml) ใช้ร่วมกันทั้ง process

### 3.4 `/api/config/status` (บางส่วน)
- คืน `{data: {provider_id: {configured: bool, tier: "A"|"B"|"C", capabilities: [...]}}}` **ห้ามคืนค่า key** (ส่วน LLM เพิ่มใน P04)

### 3.5 Tests
- ทุก provider มี fixture test อย่างน้อย: success, empty → NoData, 429 → RateLimited, 401 → NotConfigured/BadRequest และ timeout → Transient
- router integration: ปิด yfinance (fake Transient) แล้ว PRICES ต้องไปที่ financial_datasets/alpha_vantage ตามลำดับ และ attempts ถูกต้อง
- test ว่า provider ที่ไม่มี key **ไม่ถูกเรียก HTTP เลย** (respx assert_all_called=False + route ไม่ถูก hit)
- jev_screen: mock API แล้วเช็กว่าโพสต์นอกเรื่องถูกตัดและ stance ถูกนับ
- live tests (`-m live`) ต่อ provider จะ skip ถ้าไม่มี key

## Definition of Done
- [ ] ใส่แค่ `FRED_API_KEY` แล้ว `router.get(MACRO, series=DGS10)` ได้ข้อมูล
- [ ] ไม่ใส่ key ใดเลย ระบบทั้งหมดต้องยังทำงานด้วย Tier A
- [ ] `/api/config/status` แสดงสถานะครบ 20 provider
- [ ] coverage ของ `app/domain/data` ≥ 85%
- [ ] ไม่มี key ใดไปโผล่ใน log, exception message หรือ provenance (test ด้วยการ grep ผลลัพธ์)

## นอกขอบเขต
FinNLP, earnings calls scraping, Playwright (ดู "ที่ไม่ port" ใน data-sources.md)
