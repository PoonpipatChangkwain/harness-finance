# Research: Investor-profile taxonomy, 5-Whys goal discovery, and advisor memory architecture

Prepared 2026-10-17 for Finance Harness (Product/). For an engineer turning it into prompts, schemas and code.
Citation convention: every substantive claim links its source. **[unverified]** marks numbers that appear only in secondary sources or that I could not check against the primary text. Treat those as defaults to tune, not facts.

> **Repo fit note:** today `harness/state/memory.py` is a flat `(namespace, key) → JSON` KV store, and CLAUDE.md D2 says persistent memory is written "only from intentional steps, never automatically from conversation". The memory design in §C keeps that rule: extraction runs as an explicit `memory_extract` step that produces *candidate* items, which become active only after reconciliation (and user confirmation for sensitive categories). It is never a side effect of chat.

---

## A. Professional portfolio-management styles

### A.1 Passive / strategic allocation: concrete numbers

| Building block | Concrete rule | Source |
|---|---|---|
| **Core-satellite** | 70–90% low-cost index core plus 10–30% satellite (single stocks, thematic, active). Conservative 90/10, aggressive 70/30. Trim a satellite that drifts (e.g. 10%→25%). | [CMC Markets](https://www.cmcmarkets.com/en-gb/investing-guides/core-satellite-approach), [myetfjourney](https://myetfjourney.com/blog/core-satellite-portfolio) |
| **Bogleheads 3-fund** | Total US stock + total international + total bond. "Age in bonds" (age 30 → 30% bonds), ~20% of equity in international (30-y/o: 56/14/30). ER ~0.03–0.07%. | [White Coat Investor](https://www.whitecoatinvestor.com/bogleheads-three-fund-portfolio/), [Money Crashers](https://www.moneycrashers.com/bogleheads-3-fund-portfolio/) |
| **Glide path (Vanguard TDF)** | ~90% equity in early career, decline starting around age 40 **[age-40 point is from one secondary source]**, 50% at 65, static 30/70 at ~72. | [Vanguard workplace](https://workplace.vanguard.com/investment/strategies/tdf-glide-path.html), [Vanguard retirement income](https://workplace.vanguard.com/401k-plans/retirement-income/products-and-solutions.html), [ERN](https://earlyretirementnow.com/swr19) |
| **All-Weather / risk parity** | 30% stocks / 40% long Treasuries / 15% intermediate Treasuries / 7.5% gold / 7.5% commodities. Allocates equal *risk*, not equal dollars. Suffers when rates rise (e.g. 2022). | [fffinstill](https://fffinstill.com/learning/concepts/all-weather-risk-parity-dalio), [heygotrade](https://www.heygotrade.com/en/blog/ray-dalios-all-weather-portfolio) |
| **60/40** | Baseline balanced mix. Vanguard's 1926–2014 test of a 50/50 portfolio: unrebalanced drifted to **81% equity** with 13.2% vol; annual check plus a 5% threshold gave the **lowest vol, 9.8%**. | [WealthManagement / Vanguard](https://www.wealthmanagement.com/investment/art-rebalancing) |
| **Rebalancing: calendar vs threshold** | Vanguard: annual or semiannual monitoring with **5% bands** is a reasonable risk/cost balance. Monthly with no threshold = 1,008 rebalances vs 15 for annual/10%. Tighter bands mean lower tracking error and higher cost. | [Vanguard 2010 best practices (PDF)](https://indexacapital.com/bundles/unaiadvisor/docs/papers/2010-Vanguard-Best-practices-for-portfolio-rebalancing.pdf?v=241), [Vanguard tuning frequency](https://corpaemdisp.essp.c1.vanguard.com/content/corporatesite/us/en/corp/articles/tuning-frequency-for-rebalancing.html) |
| **5/25 rule (Swedroe)** | Rebalance when an asset drifts by **5 pp absolute or 25% of its target, whichever is smaller**. A 10% sleeve gets a 7.5–12.5% band; a 60% sleeve gets 55–65%. | [A Wealth of Common Sense](https://awealthofcommonsense.com/2014/03/larry-swedroe-525-rebalancing-rule/) |

**Engineering default:** `band(target) = min(0.05, 0.25 * target)`, checked on every portfolio load and on the weekly job. Calendar fallback: an annual "review" nudge. This is deterministic compute (F1) and belongs in `domain/compute` next to `rebalance_plan`.

### A.2–A.3 Active and named styles: playbook table

Columns: core rules · holdings/concentration · turnover & cadence · risk controls · who it suits · red flags the advisor should raise.

| Style | Core rules (with numbers) | Holdings / concentration | Turnover / cadence | Risk controls | Suits | Red flags to warn about |
|---|---|---|---|---|---|---|
| **Passive core (Bogle / 3-fund)** | Broad index, minimal cost; age-in-bonds; ~20–40% of equity international ([WCI](https://www.whitecoatinvestor.com/bogleheads-three-fund-portfolio/)) | 3–4 funds | Rebalance on 5/25 bands or yearly | Allocation *is* the risk control; no stops | Any horizon ≥5y; low time commitment; low-engagement temperament | Abandoning the plan in drawdowns; home bias (Thai retail = SET-only) |
| **Core-satellite** | 70–90% core, 10–30% satellites ([CMC](https://www.cmcmarkets.com/en-gb/investing-guides/core-satellite-approach)) | Core funds + 5–15 satellites | Core yearly; satellites as needed, capped | Satellite cap (e.g. 30% total, 5% per name); trim drift | Wants to "play" without risking the plan; moderate time | Satellite creep past its cap; the satellite becomes the portfolio |
| **Value (Buffett/Munger)** | Margin of safety, "cornerstone of investment success" ([Morningstar](https://morningstar.ca/ca/news/211343/how-to-invest-like-warren-buffett.aspx)); circle of competence; 20-slot punch card as a mental exercise ([Fool](https://www.fool.com/investing/general/2004/05/05/warren-buffett-and-his-20-punches.aspx)); quality business at a fair price | Concentrated, ~10–20 names; top-5 often >50% **[typical, not a rule]** | Very low; hold for years | Valuation discipline plus business-understanding checks; no price stops | 10y+ horizon; patient; high research time | "Cheap" ≠ value (value traps); buying outside competence; anchoring on cost price |
| **Deep value (Graham net-nets)** | Buy below **2/3 of NCAV** (current assets − total liabilities) ([Validea](https://blog.validea.com/?p=1089), [Fool](https://www.fool.com/investing/general/2004/02/09/play-it-safe-the-ben-graham-way.aspx)); defensive screen P/E ≤15, P/B ≤1.5, current ratio ≥2 **[secondary impl.]** | Broad basket, 20–30+ (diversify the junk) | Annual | Diversification; 1-year hold rule | Contrarian, unbothered by ugly businesses | Illiquid micro-caps; few or no qualifying names in rich markets ([Validea](https://blog.validea.com/?p=1089)) |
| **Growth (Lynch)** | 6 categories: slow growers (~GDP), stalwarts (~10–12%), fast growers (20–30%+), cyclicals, turnarounds, asset plays ([Value Research](https://www.valueresearchonline.com/web-stories/6-stock-categories-of-peter-lynch/)); PEG ≈1 fair, <0.5 attractive; PEGY = P/E ÷ (growth + yield) ([AAII](https://www.aaii.com/stockideas/article/13717-lynchs-one-up-on-wall-street-inspired-screening-strategy)); invest in what you know | Can be wide (Lynch held hundreds), but retail should cap at ~15–25 | Moderate; re-check the "story" quarterly | Sell when the story breaks, not on price alone | Curious, follows companies; medium-high time | "I use the product" ≠ thesis; overpaying for growth; mislabelling a cyclical as a grower |
| **Quality / compounders (Terry Smith, Akre)** | "Buy good companies, don't overpay, do nothing." High, sustainable cash ROCE ([Canaccord](https://canaccordgenuity.com/wealth-management-uk/news-and-insights/talking-success-with-terry-smith)). Akre 3-legged stool: business returns, management, reinvestment runway ([Novel Investor](https://novelinvestor.com/chuck-akres-three-legged-stool)) | ~20–30 names **[Fundsmith ~29 at one point; unverified current count]**; Akre ~20 ([valueresearch](https://www.valueresearchonline.com/stories/28096/chuck-akre-gives-his-strategy-for-finding-wealth-compounders/)) | Very low: Fundsmith turnover **2.2% (2022)** vs 30–40% typical ([Canaccord](https://canaccordgenuity.com/wealth-management-uk/news-and-insights/talking-success-with-terry-smith)) | Exit if a stool leg breaks (moat shrinking, bad management, runway gone) | Long horizon; low activity; tolerates multi-year underperformance | Paying any price for quality; sector clumping (all tech/staples) |
| **GARP** | Lynch-style PEG ≤1 plus quality filters (ROE, debt) | 15–30 | Quarterly review | Valuation ceiling | Middle-ground temperament | Growth-estimate optimism makes PEG meaningless |
| **Magic formula (Greenblatt)** | Rank on earnings yield (EBIT/EV) + ROC (EBIT/(NFA+WC)); exclude financials and utilities; build **20–30 stocks** buying 5–7 every 2–3 months; hold ~1 year ([quant-investing](https://www.quant-investing.com/blog/magic-formula-complete-guide/), [AAII](https://www.aaii.com/journal/article/magic-formula-stocks)) | 20–30 equal weight | Annual per lot | Mechanical; commit 3–5y+ | Rules follower; low judgement; low time | Overriding the rules after bad years; backtest edge has shrunk ([einvesting](https://einvestingforbeginners.com/magic-formula-to-beat-market-daah/)) |
| **Momentum: Dual Momentum (Antonacci GEM)** | Monthly: US vs international 12-month return (relative), winner vs T-bills (absolute); if winner < T-bills go to bonds ([AWOCS](https://awealthofcommonsense.com/2015/07/my-thoughts-on-gary-antonaccis-dual-momentum/), [Alpha Architect](https://alphaarchitect.com/2015/02/asset-allocation-horserace-robust-asset-allocation-raa-vs-dual-momentum/)) | 1 of 3 funds at a time | Monthly check; few switches per year | Absolute-momentum filter is the stop | Disciplined; accepts whipsaw | Whipsaw in choppy markets; lagging at turns |
| **Cross-sectional 12-1 momentum** | Rank on t-12..t-2 returns (skip latest month for reversal) ([freefloat](https://freefloat.substack.com/p/the-momentum-factor)) | Top decile/quintile | Monthly, high turnover | Vol scaling; crash awareness: Mar–May 2009 losers +163% vs winners +8% ([Daniel–Moskowitz](https://business.columbia.edu/sites/default/files-efs/pubfiles/4607/mom11.pdf)) | Systematic, cost-aware | Momentum crashes after bear-market rebounds; costs and taxes |
| **Dividend / income** | Payout ratio sustainable ~30–60% (REITs higher); >100% is a red flag ([Fool](https://www.fool.com/terms/y/yield-trap)); check FCF and debt; long streaks help but don't prevent cuts (Walgreens, 3M) ([InvestorMint](https://investormint.com/small-business/dividend-trap-high-yield-stocks)) | 15–30, sector-capped | Low | Sector cap (banks, utilities, REITs cluster); yield-trap filter | Retirees, cash-flow seekers; Thai retail's default | Chasing the highest yield; ignoring total return; ex-div date trading |
| **All-Weather / risk parity** | 30/40/15/7.5/7.5 ([fffinstill](https://fffinstill.com/learning/concepts/all-weather-risk-parity-dalio)) | 5 ETFs | Yearly or bands | Risk-balanced by construction | Wants a smooth ride; low time | Rate-shock years (2022); retail can't replicate leverage |
| **Howard Marks (cycle-aware)** | Second-level thinking (what is priced in?); market psychology swings like a pendulum and "almost never stays at the midpoint"; timing a recovery is unknowable, its arrival is not ([antoinebuteau](https://www.antoinebuteau.com/lessons-from-howard-marks/), [Columbia UP](https://cupola.columbia.edu/978-0-231-15368-3)) | N/A (a lens, not a portfolio) | Tilt defensive/aggressive by cycle position | Risk = permanent loss, not volatility | Experienced investors | Using "cycle" talk to justify market timing |
| **Druckenmiller (concentration + sizing)** | "Put all your eggs in one basket and watch the basket very carefully"; 35–40 names is where you get in trouble; sizing is "70–80% of the game" ([Benzinga](https://www.benzinga.com/25/05/45487510/stanley-druckenmiller-once-said-great-investors-put-their-eggs-in-one-basket-explaining-why-they-then-watch-the-basket-closely), [Marcellus](https://marcellus.in/story/stanley-druckenmiller-the-greatest-investors-make-large-concentrated-bets-where-they-have-a-lot-of-conviction/)) | Very concentrated | High, macro-driven | Fast cutting of losers; liquidity | Professionals only | Retail copying the concentration without the risk management |
| **Taleb barbell** | 85–90% ultra-safe (T-bills) + 10–15% highly convex speculative bets, nothing in the middle ([Shortform](https://www.shortform.com/blog/?p=4253)) **[popular summary, not a book quote]** | Few safe holdings + many small bets | Speculative sleeve churns | Max loss bounded by sleeve size | Anyone wanting bounded downside on speculation | Calling a 50% crypto position a "barbell"; the safe side must actually be safe |

**How to turn this into code:** create `domain/agents/styles/<style_id>.yaml` with `rules[]`, `holdings_range`, `max_position_pct`, `rebalance`, `red_flags[]`, `suits{horizon_min_y, time_commitment, temperament}`. The advisor prompt pulls the user's `investment_style` memory, injects that style's red flags as a checklist, and *policy* (`domain/policy`) enforces the numeric caps. Example defaults: max position 10% for passive/core-satellite satellites, 20% for value/quality with an explicit user opt-in.

### A.4 Risk profiling best practice

**Three separate constructs. Never blend them into one score** ([Kitces](https://www.kitces.com/blog/goal-risk-tolerance-mismatch-need-capacity-accommodative-authoritative-portfolio-design/), [FPA Journal](https://www.financialplanningassociation.org/article/journal/OCT16-incorporating-financial-risk-tolerance-research-financial-planning-process), [Financial Planning](https://www.financial-planning.com/news/are-you-getting-it-wrong-on-risk-tolerance)):

| Construct | What it measures | How to get it | Our field |
|---|---|---|---|
| Risk **tolerance** (willingness) | Psychological comfort with loss | Psychometric questionnaire | `risk_tolerance_score` 1–10 |
| Risk **capacity** (ability) | Ability to absorb loss without derailing goals: horizon, liquidity needs, income stability, other assets | Financial facts, not attitude questions | `risk_capacity_score` (computed) |
| Risk **need** (required return) | Real return needed to hit a goal given PV, contributions, horizon, target ([Stratifi](https://www.stratifi.com/glossary/risk-needed)) | Deterministic calc | `required_return` (compute) |

**Recommended portfolio risk = min(tolerance, capacity)**, then compare it with need. If need > min(tolerance, capacity), the advisor must surface the gap: save more, extend the horizon, or lower the goal. It must not silently take more risk.

**What robo-advisors do:**
- Wealthfront: risk score **0.5–10** from age, income/net worth, and subjective tolerance ([Wealthfront](https://www.wealthfront.com/blog/ask-wealthfront-risk-score-explainer)).
- Betterment: questionnaires measure "what kind of risk-taker you think you are, not what you need to be"; it weights **time horizon and withdrawal style (lump sum vs gradual)** most heavily ([Betterment](https://www.betterment.com/resources/tag/investing-risk)).
- Schwab: separate **Time Horizon score × Risk Tolerance score** grid. The institutional IPQ has 11 questions mixing Risk Willingness (behavioural) and Risk Capacity (factual), and lets the client move ±1 portfolio ([Schwab IPQ](https://institutionalintelligent.schwab.com/public/intelligent-advisor/learn-more/whitepapers/investor-profile-questionnaire.html)).

**KYC fields (FINRA 2111, non-exhaustive):** age, other investments, financial situation and needs, tax status, objectives, experience, time horizon, liquidity needs, risk tolerance, plus anything else the customer discloses ([FINRA 2111](https://www.finra.org/rules-guidance/rulebooks/finra-rules/2111)). FINRA also flags *quantitative suitability* (turnover, cost/equity ratio, in-and-out trading). That gives a direct mandate to warn over-traders.

**Behavioural biases: detection signal → coaching move**

Vanguard estimates behavioural coaching at **~150 bps/yr, half of "advisor's alpha" (~3%)** ([Vanguard](https://www.vanguard.ca/content/dam/intl/americas/canada/en/documents/gas/quantifying-your-value-to-clients-advisor.pdf)). DALBAR: average equity investor **16.54% vs S&P 25.02% in 2024** (−848 bps), narrowing to −72 bps in 2025 ([DALBAR 2025](https://www.dalbar.com/Portals/dalbar/Cache/News/PressReleases/2025QAIBPressRelease.pdf), [DALBAR 2026](https://www.dalbar.com/PressReleases/doc/2026QAIBReportPressRelease.pdf)). Barber (2009) argues DALBAR overstates the gap ([WealthManagement](https://www.wealthmanagement.com/client-relations/maybe-you-re-not-as-stupid-as-your-financial-adviser-thinks)).

| Bias | Evidence | Detectable signal in our data | Coaching move (prompt guidance) |
|---|---|---|---|
| Loss aversion | λ≈2.25 (T&K 1992), meta-analysis mean ≈1.96 ([healthcare-economist](https://healthcare-economist.com/2024/06/26/empirical-estimates-of-loss-aversion)) | Refuses to sell losers; asks for "safe" after a small dip | Reframe as portfolio-level and goal-level loss; show the probability of hitting the goal, not the daily P&L |
| Disposition effect | Gains realized at 14.8% vs losses at 9.8% (≈1.5×) ([Odean 1998 / Wikipedia](https://en.wikipedia.org/wiki/Disposition_effect)) | Sell requests concentrated in winners; long-held losers | "Would you buy this today at this price?" test; ignore cost basis in the thesis check |
| Overconfidence / over-trading | Top-turnover households earned 11.4% vs market 17.9% ([Barber & Odean](https://faculty.haas.berkeley.edu/odean/papers/returns/returns.html)) | Turnover >100%/yr; many trade requests per week | Show cost and tax drag computed from their own trades; suggest a capped "fun money" sleeve |
| Recency | — | Extrapolates last 3–12 months; "it always goes up" | Show the full-cycle drawdown of the asset (MDD over 10y+) |
| Herding / FOMO | — | Requests correlated with trending tickers or social sentiment (we already ingest Reddit/StockTwits/X) | Ask the 5-Whys (§B); check whether it fits their style and caps |
| Home bias (Thai-specific) | — | >80% SET exposure | Show correlation and concentration; suggest a global core via a fund/ETF |

### A.5 Thai retail context (brief)

- **Board lot:** shares priced below THB 500 trade in lots of **100**. After 6 consecutive months at ≥THB 500 the lot becomes 50. Smaller quantities are odd lots ([Clearstream](https://clearstream.com/clearstream-en/res-library/market-coverage/securities-trading-thailand-1280838)). The existing `rebalance_plan` (lot 100) should become price-dependent.
- **Market structure:** Dec 2025 trading value split: foreign **54.1%**, local retail **29.0%**, local institutions 10.7%. Foreigners were net sellers of THB 107.1bn in 2025. Average daily value was THB 41.4bn ([SET via Mondo Visione](https://mondovisione.com/news/set-market-report-for-december-and-full-year-2025-202618/)). The SEC notes domestic trading activity continues to decline ([SEC strategic plan](https://www.sec.or.th/EN/Documents/AboutSEC/strategicplan-2026-2028-en.pdf)).
- **Tax funds:**
  - **SSF incentive ended 31 Dec 2024.**
  - **RMF:** 30% of income, capped at THB 500k *shared* with PVD/GPF/pension insurance. Hold to age 55 and ≥5 years.
  - **ThaiESG:** normally 30% of income up to THB 100k, raised to **THB 300k for 2024–2026 purchases** with a 5-year hold.
  - **ThaiESGX:** limits conflict across sources **[verify against the Revenue Dept before encoding]**.
  - Sources: [Bangkok Bank](https://www.bangkokbank.com/en/Personal/Save-And-Invest/Mutual-Funds/RMF-SSF-ThaiESG), [moneymgmnt](https://www.moneymgmnt.com/news/thailand-investment-tax-incentives-2025/), [brokerth](https://brokerth.com/en/esg-rmf-2026-tax-deadline-guide-thai-professionals-en/).
  - The advisor should treat tax-fund units as *locked* positions (non-sellable before the lock date), which fits the existing `NoSellList` policy.
- **Suitability:** the Thai SEC requires a standardized suitability test covering financial status, knowledge/experience, objective and risk appetite, with basic asset allocation per risk level. One test covers all products, typically refreshed every **2 years** ([SEC TH](https://sec.or.th/EN/Pages/News_Detail.aspx?SECID=5440), [Coins](https://support.coins.co.th/hc/en-us/articles/360037973031-Why-Must-I-Complete-the-Coins-Suitability-Test-Questionnaire)). Re-prompt profiling every 24 months or on life events.
- **Behaviour:** a dividend-heavy culture (banks/energy), year-end tax-fund buying, and high SET home bias. Bank stocks move on dividend news ([Kasikorn Research](https://www.kasikornresearch.com/en/analysis/k-econ/financial/Pages/MSMR240225.aspx)).

---

## B. "5 Whys" goal discovery for financial advising

**Grounding:**
- Practitioner precedent: Bill Bacharach's "Why does money matter to you? … why does that matter?" chain. Kitces cautions that it works best when clients expect that style ([Kitces](https://www.kitces.com/blog/statement-of-financial-purpose-goals-values-based-meaninfgul-action-planning-reflective-quetsions-communication-discovery/)).
- George Kinder's 3 life-planning questions (ideal future → goals it needs → what to do now) ([Kitces](https://kitces.com/?p=226428)).
- Motivational Interviewing's **OARS**: Open questions, Affirmations, Reflections, Summaries. Unsolicited advice and persuasion backfire with ambivalent clients ([NAPFA](https://www.napfa.org/conference-session/160/120523), [CFP study guide](https://open-exam-prep.com/study-guides/cfp/psychology-of-financial-planning/therapeutic-techniques)).
- Trust before depth: rushing values talk early backfires ([Kitces](https://www.kitces.com/blog/statement-of-financial-purpose-goals-values-based-meaninfgul-action-planning-reflective-quetsions-communication-discovery/)).

### B.1 Rules for the LLM

1. **Never ask a bare "why?"** Use reflect + open question: "Sounds like NVDA's run caught your eye. What would owning it do for you?" Rotate forms: "what would that give you", "what happens if it doesn't work out", "how does this fit with…".
2. **Max 5 whys per goal thread, max 2 per turn, and only 1 question per message.** Always offer a skip: "or I can just analyze it now."
3. **Answer first, then ask.** If the user asked a factual question (price, news), give it, then ask one why. Never hold information hostage to discovery.
4. **Use memory before asking.** If an active memory already answers the question (e.g. goal "house down-payment 2028"), confirm it ("Still saving for the 2028 condo?") instead of re-asking.
5. **Stop when any stop condition fires** (below), then summarize the root goal back (MI "summary") and get a yes or a correction. A confirmed summary is what gets written to memory as a candidate.

### B.2 Root-goal signals (stop asking)

- The answer names a **terminal value or life outcome**: security, family, freedom, retirement, legacy, an education fund, buying a home, no longer worrying about money.
- The answer has **amount + horizon** (e.g. "THB 2m by 2030").
- The answer contains a **constraint/emotion pair** that explains the request ("I can't stand seeing −20% again").
- **Repetition:** the next answer restates the previous one.
- **Resistance:** "just tell me", short or irritated replies, or a skip click.
- **Urgency/safety:** crash panic, margin call, or distress. Switch to stabilise mode: facts, options, no more whys.

### B.3 Decision rule: ask again vs proceed

```
proceed_with_advice if any of:
  root_goal_identified and confidence >= 0.7
  whys_asked >= 5
  user_resistance detected
  urgency in {"high"}  (panic / time-critical) -> stabilise mode
  request is purely informational (no allocation decision implied)
ask_another_why if:
  request implies an allocation change (buy/sell/size/all-in) AND
  confidence < 0.7 AND whys_asked < 5 AND no resistance
```

### B.4 Output schema (structured, T5-style)

```json
{
  "request_type": "buy_single | sell_all | concentrate | return_target | income | info | other",
  "whys_asked": 2,
  "chain": [{"q": "...", "a": "..."}],
  "root_goal_identified": true,
  "root_goal": {"category": "retirement|home|education|income|wealth_growth|safety|speculation_fun|legacy|other",
                "amount": 2000000, "currency": "THB", "horizon_years": 5, "text": "..."},
  "emotional_driver": "fomo | fear | greed | boredom | none",
  "confidence": 0.78,
  "next_question": null,
  "stop_reason": "root_goal | max_whys | resistance | urgency | informational | memory_already_known",
  "advice_mode": "normal | stabilise | educate",
  "memory_candidates": [{"category": "goal", "text": "...", "confidence": 0.78}]
}
```
Fail-safe: if this fails to parse, treat it as `root_goal_identified=false, advice_mode=normal` and proceed with advice plus generic caveats. Never block the user.

### B.5 Example chains

| Request | Why 1 → answer | Why 2 → answer | Why 3 → answer | Root / stop | Advice pivot |
|---|---|---|---|---|---|
| "I want to buy NVDA" | What would owning it do for you? → "It's up a lot, I don't want to miss more." | If it kept rising without you, what would that cost you? → "I'm behind on retirement savings." | — | Root: retirement catch-up; driver: FOMO; conf 0.75 | Required-return calc; satellite cap 5–10%; DCA; check tech concentration |
| "Sell everything, market's crashing" | (urgency → stabilise) What are you most worried will happen? → "Losing my kids' tuition money." | When is the tuition due? → "Two years." | — | Root: education fund in 2y (capacity issue, not tolerance) | Move only the 2-year liability to cash/short bonds; keep the long-horizon money; show drawdown history |
| "Go all-in on gold / crypto" | What would that give you that your current mix doesn't? → "Protection from baht/inflation." | How much protection would let you sleep? → "Maybe a quarter." | — | Root: inflation hedge; tolerance ≈ 25% | Barbell framing: cap the hedge sleeve 5–15%; correlation evidence |
| "I want 20%/year" | What would 20% let you do? → "Retire at 50." | What's the gap if it were 8%? → "Retire at 56, I guess." | Which matters more: age 50, or not risking big losses? → "Not losing big." | Root: early retirement, but tolerance outranks the target; conf 0.8 | Show required return vs probability; options: save more or retire later |
| "I want dividends" | What will the dividends pay for? → "Monthly expenses after I stop working." | When? → "In 10 years." | — | Root: retirement income in 10y | Total-return vs yield; yield-trap filter; sector cap on banks/energy; RMF for tax |

---

## C. Memory architecture

### C.1 Mem0: how it works (and how it changed)

- **Paper pipeline** ([arXiv 2504.19413](https://arxiv.org/html/2504.19413v1)):
  - **Extraction:** inputs are the new message pair, an asynchronously refreshed conversation summary, and the last **m=10** messages. Output is a list of candidate facts.
  - **Update:** for each fact, retrieve the top **s=10** similar memories, then an LLM tool-call picks **ADD** (no equivalent exists), **UPDATE** (complements existing), **DELETE** (contradicts), or **NOOP**.
  - Mem0 deletes contradicted memories. Mem0g (graph variant, Neo4j triplets) instead **marks conflicting edges invalid** to preserve history.
  - Results with GPT-4o-mini on LOCOMO: J 66.9% vs full-context 72.9%, but **p95 latency 1.44s vs 17.1s** and ~7k memory tokens per conversation.
- **Prompts** ([mem0 prompts.py](https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/configs/prompts.py)):
  - `FACT_RETRIEVAL_PROMPT` returns `{"facts":[...]}`, takes facts only from user/assistant messages (never system), and records them in the user's language.
  - `DEFAULT_UPDATE_MEMORY_PROMPT` returns `{"memory":[{"id","text","event":"ADD|UPDATE|DELETE|NONE","old_memory"}]}`. It keeps the same ID on update and never invents IDs. "Likes cheese pizza" vs "Loves cheese pizza" → no update. "Loves" vs "Dislikes cheese pizza" → DELETE.
- **2026 change:** Mem0 replaced the two-pass reconcile with a **single-pass ADD-only** extractor (`ADDITIVE_EXTRACTION_PROMPT`).
  - Their stated reason: UPDATE/DELETE "destroyed information" and was slow. Latency roughly halved.
  - Old and new facts now coexist, and recency, entity, and keyword signals rank them at retrieval. Hybrid retrieval fuses semantic + BM25 + entity scores.
  - Each item is 15–80 words, with pronouns resolved to "User", relative dates resolved against the observation date, and `linked_memory_ids` / `attributed_to` fields.
  - Sources: [mem0 blog](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm), [how-it-works](https://docs.mem0.ai/core-concepts/how-it-works.md).
  - A third-party study also found frequent JSON parse failures in UPDATE/DELETE that failed silently ([arXiv 2606.15903](https://arxiv.org/pdf/2606.15903)).
- **API and self-hosting:**
  - Apache-2.0. `Memory()` / `Memory.from_config(cfg)`, `add(messages, user_id=…)`, `search(query, filters={"user_id":…}, top_k=…)`, `get_all(user_id=…)`, `update`, `delete`, `history`, `reset` ([PyPI](https://pypi.org/project/mem0ai/2.0.13/), [OSS quickstart](https://docs.mem0.ai/open-source/quickstart)).
  - Fully local via Ollama (LLM + `nomic-embed-text`). The OpenAI provider accepts `openai_base_url`, so any OpenAI-compatible endpoint works ([Mem0+Ollama](https://docs.mem0.ai/examples/mem0-with-ollama), [LLM config](https://docs.mem0.ai/components/llms/config)).
  - It brings a vector DB dependency and its own LLM calls outside our provenance/budget layer. **Recommendation: port the pattern rather than adding the dependency** (it would also bypass `harness/llm` failover and usage accounting).

**Lesson for us:** in finance, the *history* of preferences matters (risk tolerance after a crash vs before). Mem0's own reversal argues for **never hard-deleting**: supersede and retire instead.

### C.2 ReasoningBank and related

- **ReasoningBank** (Google, ICLR 2026; [arXiv 2509.25140](https://arxiv.org/html/2509.25140v1), [Google blog](https://research.google/blog/reasoningbank-enabling-agents-to-learn-from-experience/)):
  - Each item has **title** (concise strategy id), **description** (one sentence), **content** (distilled reasoning steps or insights).
  - Items come from **both successful and failed** trajectories. Success prompts ask "why it succeeded → transferable strategy"; failure prompts ask "causes → preventive lessons". Each trajectory yields **≤3 items**, extracted at temperature 1.0.
  - Success is judged by **LLM-as-judge** (binary, temperature 0) given the query, trajectory, final state and output.
  - Retrieval embeds the query (gemini-embedding-001), uses cosine similarity, and injects **top-k=1** into the system prompt. Ablation: 0 items 39.0 → **1 item 49.7** → 2/3/4 items 46.0/45.5/44.4. More is worse.
  - Consolidation is plain append (no merge or forget).
  - **MaTTS** comes in two forms. Parallel: k trajectories per query, with self-contrast between successes and failures (49.7 → 55.1 at k=5). Sequential: self-refinement notes (54.5 at k=5).
  - Results: WebArena +8.3/+7.2/+4.6 pts across backbones; SWE-bench-Verified 34.2→38.8 (Flash); ~16% fewer steps.
- **Reflexion:** verbal self-reflection stored in an episodic buffer and fed into the next trial. HumanEval 91% pass@1 vs 80% for GPT-4 ([arXiv 2303.11366](https://arxiv.org/abs/2303.11366v4)).
- **Generative Agents memory stream:** importance rated **1–10** at write time, recency decay **0.995**/hour since last retrieval, and score = recency + importance + relevance (each min-max normalised, all weights = 1). Reflection triggers when summed importance of recent events exceeds **150** (~2–3×/day) ([arXiv 2304.03442](https://arxiv.org/html/2304.03442v2)).
- **Letta/MemGPT:** core memory blocks in context (label, description, value; edited by `core_memory_append/replace`, newer `memory_insert/replace`), plus recall (searchable chat history) and archival (vector store) ([Letta docs](https://docs.letta.com/guides/agents/architectures/memgpt)).
- **A-MEM:** Zettelkasten notes (content, context description, keywords, tags, embedding, links). Adding a note can *evolve* older notes' context and tags ([arXiv 2502.12110](https://arxiv.org/html/2502.12110v8)).

**Portable patterns for SQLite + embeddings (no Mem0):**
1. **Two tiers, like Letta.** A small, always-injected **profile block** rendered from active high-confidence items (≤600 tokens: style, risk scores, goals, constraints) plus searchable archival items.
2. **Write-time importance (GA)** plus `last_accessed_at` for recency decay. Retrieval score = `0.5·cos + 0.3·importance_norm + 0.2·recency`, then filter `status='active'`.
3. **Supersede, don't delete** (Mem0g/2026 lesson). Contradictions create a new row and mark the old one `superseded`.
4. **Reasoning bank (RB):** title/description/content items from judged outcomes, including **failures**. Inject **top-1 to top-2 only**.
5. **Embeddings:** store a `BLOB` (float32) in SQLite and do brute-force cosine in numpy. That is fine to ~50k rows. Add `sqlite-vec` later. Add an FTS5 table for the keyword leg (hybrid, like Mem0 2026).

### C.3 Recommended schemas

**(a) `user_memory`** (per user; new table, migration 0006)

| Field | Type | Notes |
|---|---|---|
| id | TEXT (ulid) | |
| user_id | TEXT | single-user today; keep the column |
| category | ENUM | `investment_style, risk_tolerance, risk_capacity, goal, constraint, preference, life_fact, behavioral_pattern, knowledge_level` |
| key | TEXT nullable | slot for single-valued facts (`risk_tolerance.score`, `goal:house_2028`), so slot collisions mean contradiction |
| text | TEXT | self-contained, 15–80 words, "User …" (Mem0 style) |
| value_json | JSON | structured payload, e.g. `{"score":6,"scale":10}` or `{"amount":2000000,"ccy":"THB","horizon_y":5}` |
| confidence | REAL 0–1 | stated explicitly = 0.9; inferred from behaviour = 0.5–0.7 |
| importance | INT 1–10 | GA-style, set at write time |
| source | ENUM | `user_stated, user_confirmed, inferred_behavior, questionnaire, portfolio_data` |
| source_ref | JSON | `{session_id, message_id, run_id}` (provenance F3) |
| status | ENUM | `candidate, active, superseded, retired, rejected` |
| supersedes / superseded_by | TEXT fk | chain for history |
| valid_from / valid_to | DATETIME | temporal reasoning ("before the 2025 crash you were 7/10") |
| sensitivity | ENUM | `normal, financial, pii`; `pii` is never sent cross-user and is redacted in logs |
| embedding | BLOB | |
| created_at / updated_at / last_accessed_at / access_count | | |

**(b) `reasoning_bank`** (shared across users; anonymised)

| Field | Type | Notes |
|---|---|---|
| id | TEXT | |
| title | TEXT | e.g. "Don't size a FOMO buy above the satellite cap" |
| description | TEXT | 1 sentence |
| content | TEXT | ≤150 words of strategy/steps, no PII and no exact holdings |
| kind | ENUM | `strategy` (from success), `pitfall` (from failure), `coaching` |
| outcome_signal | ENUM + REAL | `success/failure/mixed` + judge score; for trades, attach realised outcome after settle (P10 `settle_decisions`) |
| tags | JSON | `{scenario, workflow: research|portfolio|trade, asset_class, market: SET|US|FX, regime: bull|bear|sideways|high_vol, investor_style, bias}` |
| embedding | BLOB | embed `title + description` (+ tags string) |
| use_count / success_count / success_rate | INT/INT/REAL | update when a retrieved item is in a run later judged successful; use a Bayesian estimate `(s+1)/(n+2)` |
| source_count | INT | number of distinct users/runs that produced near-duplicates; **promote to retrievable only if ≥3 (k-anonymity)** |
| status | ENUM | `candidate, active, retired` (retire if success_rate < 0.35 after n ≥ 10) |
| prompt_version / model / created_at | | provenance |

**Retrieval for RB:** filter by tags (workflow + market + asset_class), cosine top-5, re-rank by `cos × (0.5 + success_rate)`, and inject the **top 1–2** (ReasoningBank: >1 hurt). Log `rb_item_ids` into run provenance so outcomes can update `success_rate`.

### C.4 Contradiction-resolution algorithm

```
for cand in extracted_candidates:              # from explicit memory_extract step
    if cand.confidence < 0.4: drop
    neighbours = active items, same user, same category,
                 (same key) OR cosine >= 0.80, top 5
    decision = LLM(RECONCILE_PROMPT, cand, neighbours)   # structured
    match decision.op:
      NOOP      -> touch neighbour.last_accessed_at; maybe bump confidence +0.05
      ADD       -> insert status=active (or candidate if category in
                   {risk_tolerance, goal, constraint} and source != user_stated)
      UPDATE    -> insert new row (merged text), old.status=superseded,
                   old.superseded_by=new.id, old.valid_to=now   # never overwrite in place
      SUPERSEDE -> same as UPDATE but text is replacement (contradiction)
      RETIRE    -> old.status=retired (user explicitly says it no longer applies)
deterministic guards (override the LLM):
  - same `key` + different value  => SUPERSEDE (no LLM needed)
  - inferred_behavior may NOT supersede user_stated; store as separate
    behavioral_pattern + flag "stated vs revealed mismatch" for the advisor
  - risk_tolerance changes within 30 days of a >10% drawdown => status=candidate,
    ask user to confirm when markets are calm (recency/loss-aversion guard)
  - hard DELETE only on explicit user "forget this" (privacy), cascades history
```

**RECONCILE_PROMPT** (adapted from Mem0's update prompt, with supersede semantics):
```
You maintain a long-term profile of ONE investor. Compare the NEW fact with EXISTING memories
(same user, same category). Choose exactly one op per new fact:
- NOOP: the new fact says the same thing (wording differences, less detail).
- ADD: genuinely new information not covered by any existing memory.
- UPDATE: the new fact adds detail to an existing memory without contradicting it
  (e.g. "saving for a house" + "budget THB 5m by 2028"). Return merged text.
- SUPERSEDE: the new fact contradicts or replaces an existing memory because the user's
  situation or preference changed (e.g. "risk tolerance 7/10" -> "after the drop I want 4/10").
  Return the replacement text. Never merge contradictory statements into one.
- RETIRE: the user says an existing memory no longer applies, with no replacement.
Rules: use existing ids only; keep amounts, currencies, dates, horizons exact; resolve relative
dates against observation_date={date}; write in third person ("User ..."); one fact per item;
do not infer risk tolerance from a single emotional message - mark confidence <= 0.5.
Return JSON: {"ops":[{"op":"...","target_id":null|"...","text":"...","value_json":{},
"confidence":0.0-1.0,"importance":1-10,"reason":"..."}]}
```

**MEMORY_EXTRACT_PROMPT** (key rules): extract only durable investor facts in the 9 categories. Skip market facts, prices and news (those belong in provenance/scratchpad, not memory). Extract from user messages only, never from assistant restatements (Mem0 anti-echo rule). Use the user's language. Output ≤5 items per turn. Set `source=user_stated` only for explicit first-person statements.

**RB_DISTILL_PROMPT** (run end, after an LLM-judge or a realised outcome): "Given the task, the trajectory summary and outcome={success|failure}, write ≤3 memory items {title, description, content}. For success: the transferable strategy. For failure: the root cause and the preventive check. Content must be generic: no names, no exact amounts, no account or position sizes. Use ranges/buckets (e.g. 'a single-stock position >20% of portfolio'), and no tickers unless the lesson is about a widely traded instrument's mechanics (e.g. SET lot size)."

### C.5 Privacy: what never crosses users

- **Never in the shared bank:** names, contact details, ID numbers, account numbers, broker names plus account; exact holdings, quantities, cost basis, portfolio value, income, net worth; exact dates tied to life events; free-text quotes from the user; anything with `sensitivity in {pii, financial}`.
- **Anonymisation pipeline** before an RB insert:
  1. LLM distillation with the generic-only rule above.
  2. A deterministic scrubber: regex for emails, phones, Thai ID (13 digits), account numbers, and currency amounts → bucket (`<THB 100k`, `100k–1m`, `>1m`); percentages rounded to the nearest 5; tickers allowed only from an allowlist of liquid instruments, or replaced by `{sector} large-cap`.
  3. A second LLM "privacy judge" pass (binary: does this identify a person or their holdings?).
  4. A **k-anonymity gate**: status `candidate` until ≥3 distinct users produce a near-duplicate (cos ≥0.85), then `active`.
- **Why:** agent memory is extractable by black-box prompting (MEXTRA, ACL 2025), and memory configuration strongly affects leakage ([arXiv 2502.13172](https://arxiv.org/html/2502.13172v2)).
- **Other rules:**
  - Per-user memory is always queried with `user_id` scoping in SQL, never by similarity alone.
  - Retrieved items are injected as *data* blocks, not instructions.
  - Keys and PII stay out of provenance/logs (existing CLAUDE.md rule).
  - Users get a "what I remember about you" view with edit/forget (Mem0 `get_all`/`delete` equivalent).

---

## D. Top 10 design decisions I'd recommend

1. **Score risk tolerance, risk capacity, and risk need separately, and set recommended risk = min(tolerance, capacity) with the "need gap" surfaced explicitly.** Blending them is the most common profiling error ([Kitces](https://www.kitces.com/blog/goal-risk-tolerance-mismatch-need-capacity-accommodative-authoritative-portfolio-design/)).
2. **Encode each investing style as a data file (rules, caps, red flags) that `domain/policy` enforces and the prompt quotes.** The LLM then explains style-consistent advice, and code enforces the numbers (F1/T4).
3. **Default rebalancing = Swedroe 5/25 bands checked weekly plus an annual review, computed deterministically.** This matches Vanguard's evidence on risk/cost balance and is trivial to implement.
4. **Make lot size price-dependent (100 below THB 500, else 50) and model tax-fund units as locked positions.** These are Thai-specific correctness issues that change trade plans.
5. **Run 5-Whys only when a request implies an allocation change, cap it at 5 (≤1 question per message), and always answer first.** Discovery that blocks information drives users away. The structured rubric keeps it bounded and testable.
6. **Use supersede-and-retire memory with `valid_from/valid_to`, never in-place overwrite or silent delete.** Mem0 itself abandoned destructive UPDATE/DELETE, and finance advice needs preference history (pre/post crash).
7. **Keep memory writes in an explicit `memory_extract → reconcile → (confirm)` graph step, with `user_confirmed` required for goals, risk and constraints.** This honours CLAUDE.md D2 and blocks one emotional message from rewriting the profile.
8. **Inject a ≤600-token always-on profile block plus top-k retrieval, and inject only the top 1–2 reasoning-bank items.** ReasoningBank showed one item beats several, and context is the scarcest resource (D1).
9. **Distill reasoning-bank items from failures as well as successes, and score them later against realised outcomes (settle_decisions), not only LLM-judge.** Failure lessons were key to ReasoningBank's gains, and realised P&L is our unique ground truth.
10. **Admit items to the shared bank only after LLM distillation, a regex scrubber, a privacy judge, and a k≥3 anonymity gate, with no holdings, amounts or quotes.** Agent memory is demonstrably extractable, so a single leak of a user's holdings would be fatal to trust.
