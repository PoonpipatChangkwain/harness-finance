# Phase 02 — Data layer core + Tier-A providers (F4, F3)

## เป้าหมาย
สร้าง data layer ที่ agent ไม่รู้ว่าข้อมูลมาจาก vendor ไหน ประกอบด้วย interface, router (failover/fanout), error taxonomy, cache, look-ahead guard และ provenance จากนั้นทำ provider ที่ไม่ต้องใช้ key ให้ใช้งานได้จริง: `yfinance`, `sec_edgar`, `polymarket`, `stocktwits`, `reddit_rss`, `web_fetch`

## ต้องเสร็จก่อน
P01

## อ่านก่อนเริ่ม
- `docs/contracts.md` §3, `docs/data-sources.md` ทั้งไฟล์
- `../TradingAgents/tradingagents/dataflows/router.py` (VENDOR_METHODS, OPTIONAL_CATEGORIES, fallback loop), `errors.py`, `config.py`, `net.py`, `date_window.py`
- `../TradingAgents/tradingagents/dataflows/vendors/yahoo/*.py`
- `../TradingAgents/tradingagents/dataflows/vendors/{sec_edgar,polymarket,stocktwits,reddit}.py`
- `../dexter/src/tools/fetch/web-fetch.ts`, `utils.ts`
- `../dexter/src/utils/cache.ts` (ถ้ามี) หรือ `../dexter/src/tools/finance/api.ts` (readCache/writeCache)

## Tasks

### 2.1 Core (`app/domain/data/`)
- `base.py`: `Capability`, error classes, `DataProvider` Protocol, `DataRequest`, `DataResult` ตาม contracts §3
- `schemas.py`: schema กลางของแต่ละ capability
  - `OHLCVFrame` (bars + interval + tz + adjusted flag), `Quote`, `CompanyProfile`, `FundamentalsSnapshot` (dict metric → value + period), `StatementSet` (income/balance/cashflow, period type, fiscal dates, filed_at?)
  - `NewsItem` (id, title, summary, url, source, published_at, tickers, sentiment?), `SocialPost` (platform, id, text, author, created_at, url, stance_label?, metrics)
  - `MacroSeries` (series_id, title, units, observations[]), `PredictionMarket` (question, outcomes[{name, prob}], volume, end_date, url)
  - `InsiderTxn`, `WebSearchHit`, `WebPage` (url, title, markdown, fetched_at), `SymbolMatch`
- `window.py`: port `date_window.py` (`to_utc`, `in_window`, `coverage_gap`) ให้ครบรวมกฎของ item ที่ไม่มีวันที่
- `cache.py`: file cache ใต้ `DATA_DIR/cache/` ใช้ key = sha256(provider, capability, canonical params, as_of) และ TTL ต่อ capability (PRICES intraday 1m, daily 6h; STATEMENTS 7d; NEWS 15m; SOCIAL 10m; MACRO 12h) โดย cache ต้องเก็บ provenance ด้วย และเมื่อ hit ให้คืน record เดิมที่ `fetched_at` เดิม
- `http.py`: `make_client()` คืน `httpx.AsyncClient` (timeout 20s, UA จาก settings) และ `request_with_retry()` ที่ retry เฉพาะ `Transient`/`RateLimited` (backoff 0.5·2ⁿ + jitter, max 3, เคารพ `Retry-After` ≤ 30s) โดย map status → error class
- `router.py`: `DataRouter`
  - โหลด `routing.yaml` (ลำดับตาม data-sources.md §2) และ override ได้จาก settings
  - `async get(cap, req, *, mode=None) -> DataResult`
    - failover: ข้าม provider ที่ `not is_configured()` หรือ `not supports()` (บันทึกเหตุผลใน notes) แล้วลองทีละตัว เจอ `NoData/Unsupported/RateLimited/Transient` ให้ไปตัวถัดไป เจอ `BadRequest` ให้หยุดทันที
    - fanout: `asyncio.gather` ทุกตัวที่ใช้ได้ รวม data (dedupe) แล้ว status = `ok` ถ้ามีอย่างน้อย 1 ตัวที่ ok, `partial` ถ้ามีบางตัว unavailable และแนบ note ว่าตัวไหนล้ม
    - capability ที่เป็น core แล้วทุกตัวล้มให้ raise `AllProvidersFailed(attempts=[...])` ส่วน optional ให้คืน `DataResult(status="unavailable")`
  - คืน `attempts: list[ProviderAttempt(provider, outcome, duration_ms, error?)]` สำหรับ UI/provenance
- `registry.py`: ลงทะเบียน provider ทั้งหมดและสร้าง router singleton

### 2.2 Providers Tier A (`app/domain/data/providers/`)
แต่ละไฟล์ต้องมี docstring ระบุไฟล์ต้นทาง และ port logic ให้ครบ

| ไฟล์ | Capabilities | จุดที่ต้องระวัง |
|------|--------------|----------------|
| `yfinance_provider.py` | PRICES, QUOTE, PROFILE, FUNDAMENTALS, STATEMENTS, NEWS, GLOBAL_NEWS, INSIDER, ANALYST, SYMBOL_SEARCH | ห่อด้วย `asyncio.to_thread`, port `yf_retry`, `raise_for_empty` และ `filter_financials_by_date` (as_of), รองรับ `.BK`/`=X`/`-USD`/`^`, interval `1d,1wk,1h,15m`, แปลง timezone เป็น UTC และเก็บ tz เดิม |
| `sec_edgar_provider.py` | STATEMENTS (point-in-time), FILINGS | ต้องมี `SEC_EDGAR_USER_AGENT` ไม่งั้น `NotConfigured`, ticker→CIK map cache 1 วัน, symbol ที่ไม่ใช่ US → `Unsupported`, เคารพ rate 10 req/s |
| `polymarket_provider.py` | PREDICTION_MARKETS | Gamma API, `query`/`tag` params, top N ตาม volume |
| `stocktwits_provider.py` | SOCIAL | map crypto base ตาม `crypto_base`, ใช้ `in_window` และ label Bullish/Bearish |
| `reddit_rss_provider.py` | SOCIAL | Atom parse, 429 → backoff ครั้งเดียวตาม Retry-After และล้มต้องเป็น `unavailable` ห้ามคืน list ว่างแบบ ok |
| `web_fetch_provider.py` | WEB_FETCH | httpx + `readability-lxml` + `markdownify`, ตัดที่ 40k chars (บันทึก `truncated`), บล็อก scheme ที่ไม่ใช่ http(s) และ IP ภายใน (SSRF guard) |

### 2.3 Symbol search
- `SYMBOL_SEARCH` ค้นใน static CSV (SET, S&P500 top, forex majors, crypto top 20) ก่อน แล้วค่อยต่อด้วย yfinance search และคืน `SymbolMatch(symbol: Symbol, name, exchange, score)`

### 2.4 API (ย้ายมาเร็วเพื่อให้ frontend ใช้ได้)
- `GET /api/symbols/search?q=&market=`
- `GET /api/market/{symbol}/ohlcv?interval=1d&range=1y` → `{symbol, bars, provider, provenance_id}` (provenance ยังเก็บ in-memory ไปก่อน รอ P05 ทำ store)
- `GET /api/market/{symbol}/quote`

### 2.5 Tests
- `tests/domain/data/test_router.py` ใช้ fake provider 3 ตัวทดสอบ: failover ตามลำดับ, ข้าม NotConfigured, BadRequest หยุดทันที, fanout รวมผลและได้ `partial`, core ล้มทั้งหมดต้อง raise, optional ล้มทั้งหมดต้องได้ `unavailable`
- `test_window.py`: ขอบของ half-open window, item ที่ไม่มีวันที่ใน live vs backtest
- `test_cache.py`: hit/miss, TTL และ provenance เดิมถูกคืน
- provider tests ใช้ fixtures ที่อัดจาก response จริง (`tests/fixtures/providers/<id>/`) + `respx` ส่วน yfinance ให้ monkeypatch `yf.Ticker`/`yf.download`
- `@pytest.mark.live`: ดึง PTT.BK, AAPL และ EURUSD=X จริงแล้วเช็กว่ามี bars > 200

## Definition of Done
- [ ] `curl /api/market/PTT/ohlcv?range=1y` คืน bars ของ `PTT.BK` พร้อม provider = yfinance
- [ ] `curl /api/market/EURUSD/ohlcv` ได้ `EURUSD=X`
- [ ] `curl /api/symbols/search?q=ptt` เจอ PTT, PTTEP และ PTTGC
- [ ] ปิด network แล้ว cache ยังตอบได้ ถ้าไม่มี cache ต้องได้ error shape ที่ถูกต้อง
- [ ] ทุก DataResult มี provenance record ที่มี source, params, as_of และ fetched_at
- [ ] test ผ่าน โดยไม่มี network นอกจาก `-m live`

## นอกขอบเขต
Tier B/C (P03) และ tool wrapper สำหรับ LLM (P07)
