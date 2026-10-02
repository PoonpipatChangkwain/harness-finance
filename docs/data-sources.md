# Data Sources — รวมแหล่งข้อมูลจากทุก reference

รวบรวมทุกแหล่งข้อมูลที่ Dexter, TradingAgents และ FinRobot ใช้ แล้วจัดเป็น provider ของ `Product/backend/app/domain/data/providers/` ภายใต้ interface ใน [contracts.md §3](contracts.md#3-data-provider-f4)

ระดับ (Tier):
- **A — ไม่ต้องใช้ key:** ต้องใช้งานได้ตั้งแต่ติดตั้งเสร็จ และเป็นค่า default ของทุก capability
- **B — key ฟรี:** เปิดใช้เมื่อมี key ใน `.env`
- **C — key เสียเงินหรือ optional:** เปิดใช้เมื่อมี key ถ้าไม่มีต้องไม่กระทบ flow หลัก

## 1. Catalog

| # | Provider id | มาจาก (อ่านไฟล์นี้ก่อน port) | Tier | Env | Capabilities | ตลาด | หมายเหตุ |
|---|-------------|--------------------------|------|-----|--------------|------|----------|
| 1 | `yfinance` | `../TradingAgents/tradingagents/dataflows/vendors/yahoo/` (ohlcv.py, market.py, fundamentals.py, news.py, snapshot.py), `../FinRobot/finrobot/data_source/yfinance_utils.py`, `../FinRobot/finrobot_equity/core/src/modules/market_data_api.py` | A | – | PRICES, QUOTE, PROFILE, FUNDAMENTALS, STATEMENTS, NEWS, GLOBAL_NEWS, INSIDER, ANALYST, SYMBOL_SEARCH | US, **TH (`.BK`)**, forex (`=X`), crypto (`-USD`), index (`^SET.BK`, `^GSPC`), commodity (`GC=F`) | provider หลัก ใช้ `yf_retry` ตามแบบ ohlcv.py และ `raise_for_empty` → `NoData` |
| 2 | `sec_edgar` | `../TradingAgents/tradingagents/dataflows/vendors/sec_edgar.py` | A | `SEC_EDGAR_USER_AGENT` (ชื่อ + email ไม่ใช่ secret) | STATEMENTS (point-in-time as filed), FILINGS | US เท่านั้น | ไม่ต้องใช้ key แต่ต้องส่ง User-Agent และมีข้อมูล restatement ตามวันยื่นจริง เหมาะกับ backtest ส่วน symbol ที่ไม่ใช่ US ให้ `Unsupported` |
| 3 | `polymarket` | `../TradingAgents/tradingagents/dataflows/vendors/polymarket.py` | A | – | PREDICTION_MARKETS | global | Gamma API แบบ public ให้ความน่าจะเป็นของเหตุการณ์ (Fed, recession) |
| 4 | `stocktwits` | `../TradingAgents/tradingagents/dataflows/vendors/stocktwits.py` | A | – | SOCIAL | US, crypto | ใช้ label Bullish/Bearish ที่ผู้ใช้ติดเอง |
| 5 | `reddit_rss` | `../TradingAgents/tradingagents/dataflows/vendors/reddit.py` | A | – | SOCIAL | US | ใช้ RSS search เพราะ JSON โดน WAF บล็อก เมื่อเจอ 429 ให้ backoff ตาม `Retry-After` และถ้า fetch ล้มต้องคืน `unavailable` ห้ามคืน "no posts" |
| 6 | `web_fetch` | `../dexter/src/tools/fetch/web-fetch.ts`, `utils.ts` | A | – | WEB_FETCH | – | ใช้ httpx + readability ดึงบทความเป็น markdown พร้อมตัดความยาวและ cache |
| 7 | `fred` | `../TradingAgents/tradingagents/dataflows/vendors/fred.py` | B | `FRED_API_KEY` | MACRO | US | มีดอกเบี้ย, yield, CPI, แรงงาน, GDP และ clamp วันที่ตาม timezone America/Chicago |
| 8 | `alpha_vantage` | `../TradingAgents/tradingagents/dataflows/vendors/alpha_vantage/` | B | `ALPHA_VANTAGE_API_KEY` | PRICES (รวม FX_DAILY), FUNDAMENTALS, STATEMENTS, NEWS (มี sentiment), INSIDER | US, forex | free tier จำกัด ~25 req/วัน จึงควรวางไว้ลำดับหลังและ cache ให้หนัก |
| 9 | `finnhub` | `../FinRobot/finrobot/data_source/finnhub_utils.py` | B | `FINNHUB_API_KEY` | PROFILE, NEWS, FUNDAMENTALS (basic financials) | US + บางตลาด | |
| 10 | `fmp` | `../FinRobot/finrobot/data_source/fmp_utils.py`, `market_data_api.py` (EV) | B | `FMP_API_KEY` | ANALYST (target price), FUNDAMENTALS (metrics, market cap, BVPS, EV), peers | US | ใช้ใน valuation comps |
| 11 | `financial_datasets` | `../dexter/src/tools/finance/` (api.ts, stock-price.ts, fundamentals.ts, key-ratios.ts, segments.ts, filings.ts, insider_trades.ts, institutional_holdings.ts, earnings.ts, screen-stocks.ts, crypto.ts) | C | `FINANCIAL_DATASETS_API_KEY` | PRICES, STATEMENTS, FUNDAMENTALS, FILINGS (10-K/10-Q/8-K items), INSIDER, INSTITUTIONAL, ANALYST (earnings), screener | US, crypto | ข้อมูลคุณภาพสูงสุดของ US ถ้ามี key ให้ขึ้นเป็นอันดับแรกของ STATEMENTS และตัด field ซ้ำซ้อนก่อนส่ง LLM ตามแบบ `stripFieldsDeep` |
| 12 | `x_api` | `../dexter/src/tools/search/x-search.ts`, `../dexter/src/skills/x-research/SKILL.md` | C | `X_BEARER_TOKEN` | SOCIAL | global | recent search + public_metrics ต้องรอ 350ms ระหว่างหน้าและ retry 429 ได้สูงสุด 3 ครั้ง |
| 13 | `adanos` | `../FinRobot/finrobot_equity/core/src/modules/retail_sentiment_client.py` | C | `ADANOS_API_KEY` | SOCIAL (aggregate sentiment: Reddit, X, ...) | US | header `X-API-Key` |
| 14 | `exa` | `../dexter/src/tools/search/exa.ts` | C | `EXASEARCH_API_KEY` | WEB_SEARCH | – | เป็นอันดับแรกของ chain search |
| 15 | `perplexity` | `../dexter/src/tools/search/perplexity.ts` | C | `PERPLEXITY_API_KEY` | WEB_SEARCH | – | |
| 16 | `tavily` | `../dexter/src/tools/search/tavily.ts` | C | `TAVILY_API_KEY` | WEB_SEARCH | – | |
| 17 | `langsearch` | `../dexter/src/tools/search/langsearch.ts` | C | `LANGSEARCH_API_KEY` | WEB_SEARCH | – | |
| 18 | `reddit_api` | `../FinRobot/finrobot/data_source/reddit_utils.py` (PRAW) | C | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | SOCIAL | US | ได้ score และ comment ซึ่ง RSS ไม่มี ใช้แทน `reddit_rss` เมื่อมี key |
| 19 | `sec_api` | `../FinRobot/finrobot/data_source/sec_utils.py` | C | `SEC_API_KEY` | FILINGS (ดึง section ของ 10-K) | US | optional เพราะ EDGAR ครอบคลุมส่วนใหญ่แล้ว |
| 21 | `forexfactory` | (ไม่มีใน reference — เพิ่ม 2026-09-28) feed JSON สาธารณะ `nfs.faireconomy.media/ff_calendar_{thisweek,nextweek}.json` | A | – | ECONOMIC_CALENDAR | global (USD, EUR, GBP, JPY, …) | ปฏิทินข่าวเศรษฐกิจ (impact high/medium/low, forecast, previous) เฉพาะสัปดาห์นี้กับสัปดาห์หน้า ไม่มีประวัติ → as_of ในอดีตเป็น `Unsupported`; feed จำกัดความถี่ จึง cache ในหน่วยความจำ 30 นาที; ตอบ HTML แทน JSON = `Transient` |
| 22 | `spot_metals` | (ไม่มีใน reference — เพิ่ม 2026-09-28) Swissquote public bid/ask feed, สำรอง gold-api.com | A | – | QUOTE, PRICES | `XAUUSD`, `XAGUSD` (spot) | Yahoo มีแค่ futures (`GC=F`/`SI=F`) ซึ่งแพงกว่า spot ด้วย carry (~$30–40 สำหรับทอง) → QUOTE = mid ของ spot; PRICES = แท่ง futures ลบ basis (futures ล่าสุด − spot ตอนนี้) ซึ่งไม่เปลี่ยน ATR/RSI/MACD แต่ทำให้ระดับราคาตรงกับโบรก; ระดับราคาที่ย้อนไปไกลเป็นค่าประมาณ (basis เปลี่ยนช้าๆ และกระโดดตอน roll สัญญา) บันทึก basis ใน provenance + notes. พิมพ์ `GC=F`/`SI=F` ตรงๆ = futures |
| 20 | `jev_screen` (post-processor) | `../TradingAgents/tradingagents/agents/post_screen.py` | C | `TYPESAFE_API_KEY` | – (กรองผล SOCIAL) | – | ไม่ใช่ provider แต่เป็น post-processor ที่กรองโพสต์นอกเรื่องและนับ stance ถ้าไม่มี key ให้ข้าม และถ้าล้มให้ใส่ note ว่า "screening unavailable" |

### ที่ **ไม่ port** (และเหตุผล)

| แหล่ง | มาจาก | เหตุผล |
|-------|-------|--------|
| FinNLP streams (CNBC, InvestorPlace, Sina, Yicai, Xueqiu) | `../FinRobot/finrobot/data_source/finnlp_utils.py` | เป็น scraper ที่เปราะ ต้องพึ่ง package `finnlp` (submodule) และแหล่งส่วนใหญ่เป็นจีน จึงใช้ web search + web fetch แทน |
| Earnings call scraping | `../FinRobot/finrobot/data_source/earnings_calls_src/` | ใช้วิธี scrape จึงเลื่อนไปทำหลัง Phase 13 ถ้าต้องการ |
| Marker PDF pipeline | `../FinRobot/finrobot/data_source/marker_sec_src/` | ต้องใช้ GPU/หนัก ใช้ EDGAR text แทน |
| Playwright browser | `../dexter/src/tools/browser/` | เลื่อนไปทำหลัง Phase 13 เพราะ web_fetch พอสำหรับ MVP |

> **ตลาดไทย:** ไม่มี reference ไหนมี provider เฉพาะของ SET ในเฟสนี้จึงใช้ `yfinance` กับ `.BK` (ราคา, งบการเงินพื้นฐาน, ข่าวบางส่วน) และ benchmark `^SET.BK` ส่วนข่าวไทยใช้ web search แล้วกรองด้วยคำค้นภาษาไทย หากต้องการ SET official API ให้เพิ่มเป็น provider ใหม่ภายหลังได้โดยไม่แก้ harness

## 2. ลำดับ provider ต่อ capability (default)

ตั้งค่าได้ใน `backend/app/domain/data/routing.yaml` โดย provider ที่ไม่มี key จะถูกข้ามอัตโนมัติ (`NotConfigured`)

| Capability | Mode | ลำดับ |
|-----------|------|-------|
| PRICES / QUOTE (XAUUSD, XAGUSD spot) | failover | spot_metals (ตัวอื่นไม่รองรับ) |
| PRICES (stock/index/.BK) | failover | yfinance → financial_datasets → alpha_vantage |
| PRICES (forex) | failover | yfinance → alpha_vantage |
| PRICES (crypto) | failover | yfinance → financial_datasets |
| QUOTE | failover | yfinance → finnhub → alpha_vantage |
| PROFILE | failover | yfinance → finnhub → fmp |
| FUNDAMENTALS | failover | financial_datasets → yfinance → fmp → alpha_vantage → finnhub |
| STATEMENTS | failover | financial_datasets → sec_edgar (US) → yfinance → alpha_vantage |
| STATEMENTS (as_of ในอดีต) | failover | sec_edgar → financial_datasets → yfinance (+ warning เรื่อง look-ahead) |
| NEWS | fanout (dedupe by url/title) | yfinance + finnhub + alpha_vantage + financial_datasets |
| GLOBAL_NEWS | failover | yfinance → web_search |
| MACRO | failover | fred |
| PREDICTION_MARKETS | failover | polymarket |
| ECONOMIC_CALENDAR | failover (optional capability) | forexfactory |
| SOCIAL | fanout (+ jev_screen ถ้ามี key) | stocktwits + reddit_api\|reddit_rss + x_api + adanos |
| INSIDER | failover | financial_datasets → yfinance → alpha_vantage |
| INSTITUTIONAL | failover | financial_datasets |
| FILINGS | failover | sec_edgar → financial_datasets → sec_api |
| ANALYST | failover | fmp → yfinance → financial_datasets |
| WEB_SEARCH | failover | exa → perplexity → tavily → langsearch (ตามแบบ Dexter `registry.ts`) |
| WEB_FETCH | failover | web_fetch |
| SYMBOL_SEARCH | failover | local list (SET + S&P500 + forex majors) → yfinance search |

## 3. สิ่งที่ **ไม่** ดึงจาก vendor (คำนวณเองตาม F1)

- **Technical indicators** (SMA, EMA, RSI, MACD, Bollinger, ATR, VWMA ฯลฯ): TradingAgents ดึงจาก vendor/stockstats แต่ Product **คำนวณเองใน `domain/compute/indicators.py`** จาก PRICES เพื่อให้มี provenance ครบและ test ได้ ใช้ `snapshot.py` เป็นแนวทางว่าต้องมี indicator ชุดไหน
- **Market snapshot ที่ verify แล้ว** (port `build_verified_market_snapshot`): คำนวณจาก OHLCV ด้วย compute แล้วส่งให้ analyst เป็น ground truth
- Returns, volatility, drawdown, beta, correlation, weights, P&L, position size, pip value

## 4. `.env.example` (ส่วน data)

```dotenv
# --- Tier A (keyless) ---
SEC_EDGAR_USER_AGENT="FinanceHarness your-email@example.com"
# --- Tier B (free keys) ---
FRED_API_KEY=
ALPHA_VANTAGE_API_KEY=
FINNHUB_API_KEY=
FMP_API_KEY=
# --- Tier C (paid / optional) ---
FINANCIAL_DATASETS_API_KEY=
X_BEARER_TOKEN=
ADANOS_API_KEY=
EXASEARCH_API_KEY=
PERPLEXITY_API_KEY=
TAVILY_API_KEY=
LANGSEARCH_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
SEC_API_KEY=
TYPESAFE_API_KEY=
```

## 5. กฎการ port provider

1. เปิดไฟล์ reference ในตาราง §1 แล้ว port logic ของ endpoint, parameter, การ parse และการจัดการ error มาเป็น Python async (`httpx.AsyncClient`) ส่วน SDK ที่เป็น sync (yfinance, praw) ให้ห่อด้วย `asyncio.to_thread`
2. map error ของ vendor เข้า taxonomy ใน contracts §3 ให้ครบ (401/403 → `NotConfigured` หรือ `BadRequest`, 429 → `RateLimited`, 5xx/timeout → `Transient`, ผลว่าง → `NoData`)
3. แปลงผลเป็น schema กลางใน `domain/data/schemas.py` (เช่น `OHLCVFrame`, `NewsItem`, `SocialPost`, `StatementSet`, `MacroSeries`) ห้ามส่ง raw JSON ของ vendor ออกไป
4. ทุก `DataResult` แนบ `ProvenanceRecord` (source = provider id, url, params, as_of, fetched_at)
5. ใช้ `as_of` + `in_window` กับข้อมูลทุกชิ้นที่มีวันที่
6. ทุก provider มี test ด้วย recorded fixtures (`tests/fixtures/providers/<id>/*.json`) และใช้ `respx` mock HTTP ห้ามยิง network ใน unit test ส่วน test ที่ยิง API จริงให้ mark `@pytest.mark.live` และ skip ถ้าไม่มี key
