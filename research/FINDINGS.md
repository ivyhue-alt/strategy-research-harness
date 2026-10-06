# TradingBot Research Findings

Consolidated record of seven strategy families tested and closed on evidence,
plus the FTMO rule map and the MT5 data landmines discovered along the way.

**Compiled:** 2026-07-30
**Broker/feed for all research:** OANDA MetaTrader 5 demo (`OANDA-Demo-1`,
login <redacted>). **Not FTMO.** Every spread and tick-volume figure below is
OANDA's, not FTMO's.
**Method rules applied throughout:** one variable changed at a time; pass/fail
gate pre-committed in writing before each run; pessimistic intrabar (stop wins
ties); confounds measured rather than assumed; sizing fixed-fractional off
*current* equity.

---

## Contents

1. [Summary table](#summary-table)
2. [The two reusable lessons](#the-two-reusable-lessons)
3. [Family 1 — Donchian trend following](#family-1--donchian-trend-following)
4. [Family 2 — Intraday momentum (Gao/Han/Li/Zhou)](#family-2--intraday-momentum-gaohanlizhou)
5. [Family 3 — Autocorrelation reversion](#family-3--autocorrelation-reversion)
6. [Family 4 — Value-area migration continuation](#family-4--value-area-migration-continuation)
7. [Family 5 — Cointegration pair trading](#family-5--cointegration-pair-trading)
8. [Family 6 — Avellaneda-Lee residual reversion](#family-6--avellaneda-lee-residual-reversion)
9. [Family 7 — 09:00 GMT gold reversal](#family-7--0900-gmt-gold-reversal)
10. [FTMO rule map (paraphrased)](#ftmo-rule-map-paraphrased)
11. [MT5 data landmines](#mt5-data-landmines)
12. [Engine bugs found and fixed](#engine-bugs-found-and-fixed)
13. [File manifest](#file-manifest)

---

## Summary table

| # | Family | Instruments / TF | Span | Gate | Result | Verdict |
|---|---|---|---|---|---|---|
| 1 | Donchian trend | gold/US100/BTC → 7 sym, H4 | 3.8–20.4 y | RETURN/DD > 1.5 | 0.03–0.25 (breadth); 0.17 (gold 20 y) | closed |
| 2 | Intraday momentum | XAUUSD M30 | 4.2 y | any predictor significant | 0 of 12 on clean analogue | closed |
| 3 | Autocorr reversion | XAUUSD M1–H1 | 3 mo–8.5 y | ρ₁ materially ≠ 0 | ρ₁ ≈ 0 at M5–H1 | closed |
| 4 | Value-area migration | EURUSD M15/M30 | 1.19 y | win>50%, R/DD>1.5, DD<10% | 0 of 16 cells | closed |
| 5 | Cointegration pairs | 4 pairs, M30/H1/D1 | 1.2–21.6 y | majority hold@5%, EG t<−3.34, HL<20 d | 1.0–9.0% hold | closed |
| 6 | Avellaneda-Lee | 4 pairs, D1/H1 | 8.5–21.6 y | win>50%, R/DD>1.5, DD<10% | 0 of 8; best R/DD 0.11 | closed |
| 7 | 09:00 GMT reversal | XAUUSD H1 | 8.54 y | win>50%, R/DD>1.5, DD<10% | 0 of 4; null p=0.28–0.58 | closed |

**Nothing tested was profitable.** Nothing was abandoned on a hunch either —
each closure has a pre-committed gate and an identified mechanism of failure.

---

## The two reusable lessons

### Lesson 1 — Screen edge-in-R against cost-in-R *before* writing a backtest

Convert any candidate edge into expected R and compare it to transaction cost in
the same units. This single calculation would have closed several families in an
afternoon instead of a day of engine work each.

Worked example (Family 7, the one that made this explicit):

```
rho at 09:00 GMT                        = -0.131
mean |08:00 hourly move|                =  12.8 bps
=> conditional edge                     =  0.131 x 12.8 = 1.7 bps
stop unit (median 08:00 bar range)      = 26.3 bps
=> edge in R                            =  1.7 / 26.3 = 0.064 R
round-trip cost (2 x 0.55 bps)          =  1.10 bps
=> cost in R at 1.0x stop               =  0.042 R
=> cost in R at 0.5x stop               =  0.084 R
```

The edge (0.064 R) barely exceeded cost at the 1.0× stop and was **swamped** at
0.5×. Observed net expectancy was −0.049 R and −0.108 R respectively — matching
cost almost exactly, i.e. gross edge ≈ zero. The ordering was predictable from
arithmetic alone.

### Lesson 2 — A Bonferroni-surviving t-stat proves *non-zero*, not *tradable*

The 09:00 GMT reversal was the only structure in this entire project to survive
a multiple-testing correction (t = −4.35 across 24 hours). It still had **zero**
tradable content: a 200-run permutation test on the signal's sign produced
real win 48.4% vs null 48.1%, real expectancy −0.054 R vs null −0.055 R,
p = 0.28–0.58.

The t-statistic was large because **N = 1,092**, not because the effect was big
(|ρ| was only 0.06–0.15). Statistical significance and economic significance are
different quantities. Always compute the second one.

**Corollary:** include a permutation/null control in every backtest. Without it,
48.4% win rate reads as "close, needs tuning." With it, it reads as "identical to
random." Two families here (6 and 7) produced flat-looking results that the null
control revealed as indistinguishable from noise — and in Family 6 the null was
*profitable*, proving the harness could manufacture returns from a random walk.

---

## Family 1 — Donchian trend following

### Specification
- Entry: close breaks 20-period Donchian channel; exit: 55-period opposite channel
- Filter: ADX ≥ 20 (Wilder, 14)
- Stop: 2 × ATR(14)
- Risk: 1% of **current** equity; max 3 concurrent positions
- Timeframe H4; market entry at breakout-bar close
- FTMO 2-Step overlay, rolling challenge start every N bars

### Datasets
| Test | Universe | Span |
|---|---|---|
| 1a baseline | XAUUSD, US100, BTCUSD | 2022-09 → 2026-07 (3.9 y) |
| 1a breadth | + XAGUSD, USOIL, US30, US500 (7 sym) | 2022-09 → 2026-07 (3.8 y) |
| 1a short-window | + 19 FX pairs (26 sym) | 2025-06 → 2026-07 (1.1 y) |
| 1b/1c risk arch. | XAUUSD only | 2006-03 → 2026-07 (**20.4 y**) |

### Gates and results

**1a — Breadth test.** Gate: RETURN/DD > 1.5.

| config | RETURN/DD | max DD | pass rate | eff. breadth |
|---|---|---|---|---|
| 3-symbol baseline | 0.95 | −26.0% | 39% | 3.00 |
| 7-sym, list order | 0.09 | −45.4% | 10% | 6.65 |
| 7-sym, ADX tie-break | 0.03 | −44.3% | 15% | 6.89 |
| 7-sym, 4 random orders | 0.02–0.25 | −33.1 to −40.8% | 15–18% | 6.27–6.58 |

**Three headline numbers:** RETURN/DD **0.03** (ADX tie-break) · max DD
**−44.3%** · effective breadth **6.89 of 7 nominal**.

**1b/1c — Risk architecture.** Gate: 60% FTMO 2-Step pass rate.
Signal held fixed; swept risk/trade (0.25–1.00%) × daily circuit breaker
(none / flatten at −2% / −3%). 532 challenge starts per config.

| config | 3 mo | 6 mo | 12 mo | 24 mo | uncapped | median yrs |
|---|---|---|---|---|---|---|
| 0.25% risk | 0.0% | 0.0% | 0.0% | 0.0% | 59.6% | 7.5 |
| 0.75% / −2% | 3.4% | 10.2% | 29.5% | **56.6%** | 70.1% | 1.2 |
| 1.00% / −2% | 5.1% | 13.7% | 34.8% | 54.9% | 56.4% | 0.8 |

**Three headline numbers:** best 12-month pass rate **34.8%** · best 24-month
**56.6%** · gold-alone 20-year CAGR/DD **0.17**.

### Mechanism of failure
Two distinct mechanisms, in sequence.

1. **Breadth is unusable at a fixed position cap.** The book ran **79%
   slot-saturated** at MAX_OPEN=3. Adding instruments therefore did not add
   diversification — it raised average concurrent exposure (2.36 → 2.87
   positions) and filled the same three slots with lower-quality marginal
   signals. FAIL_MAXLOSS doubled, 24% → 48%. Exercising breadth requires more
   concurrent positions, which pushes directly against the 5% daily loss cap.
   **Breadth and a prop-firm concurrency cap are structurally opposed.**

   Critically, this was *not* a concentration artifact: effective breadth
   (1/HHI on position-bars) measured 6.27–6.89 of 7 nominal with average book
   correlation 0.32–0.39. Diversification genuinely happened and still lost. An
   earlier hypothesis that arbitrary fill order had concentrated the book into
   correlated metals was **refuted** by this measurement.

2. **The return/risk ratio is an order of magnitude short.** Over 20.4 years
   gold-alone Donchian H4 returns +150% total — CAGR 4.61%, max DD −26.9%,
   **CAGR/DD 0.17**. Reaching +15.5% compounded (10% then 5%) in 12 months needs
   ~16% annualised, which at CAGR/DD 0.17 implies a **~91% drawdown**; in 6
   months, ~196%. FTMO permits 10%. **The required drawdown exceeds the permitted
   drawdown by 9–20×,** and position sizing cannot close that gap because it
   scales return and drawdown together.

### Findings that changed the picture
- **Risk scaling worked exactly as first-passage theory predicts.** For a
  positive-drift random walk, P(hit target before floor) is governed by 2μ/σ²;
  scaling size by *k* scales that as 1/*k*. Dropping risk 1.00% → 0.75% raised
  pass rate 54% → 70% and drove **FAIL_DAILY from 3.2% to 0.0%**. The daily-loss
  constraint — which caused ~40% of failures in every earlier test — is solvable.
  The binding constraint then *moved* to insufficient drift.
- **The 3.9-year window was regime-flattered.** The original +151% / RETURN/DD
  1.04 came from the 2022–2026 gold bull run. Over 20 years the same signal
  yields CAGR 4.6%. 12-month pass rate by era: 25.0% (2005–09), 30.0% (2010–14),
  38.3% (2015–19), **16.5% (2020–24)**, 54.1% (2025–29 partial).
- **Levers tested and individually rejected within this family:** tp_4R,
  D1-vs-H4, MAX_OPEN 3→2 (RETURN/DD 1.04 → 0.85; DD unchanged, so concurrency
  was never the DD driver), limit-at-level entry (RETURN/DD 1.04 → 1.09 but pass
  rate 38% → 29%), same-bar entry block (neutral — retained as free insurance).

### Data/measurement limitations
- H4 history depth depends entirely on the request size: an early run asked for
  6,000 bars and got 3.9 years; asking for 50,000 returned **20.4 years**. The
  early conclusions were drawn on 19% of the available data.
- Uncapped challenge duration inflates pass rates severely (see
  [Engine bugs](#engine-bugs-found-and-fixed), item 3).

---

## Family 2 — Intraday momentum (Gao/Han/Li/Zhou)

### Specification
Replication of Gao, Han, Li & Zhou (2018), *Intraday Momentum: The First
Half-Hour Return Predicts the Last Half-Hour Return*.

- Univariate OLS, one regression per predictor bucket: `r_last = a + b·r_j + e`
- Newey-West HAC t-statistics (Bartlett kernel, lag = ⌊4(T/100)^(2/9)⌋)
- Bucket 1 return uses that bar's **open** (as in the paper); later buckets are
  close-to-close
- Two day definitions, both reported:
  - **(A)** gold's own day — 46 half-hour buckets, server 01:00–23:30, target =
    final pre-halt bucket
  - **(B)** US-session analogue — 13 buckets, 09:30–16:00 ET, target =
    15:30–16:00 ET (the equity close). **This is the true structural analogue**
    of the paper's r13.
- Splits: prior-day realised-volatility terciles (strictly ex-ante) and
  day-of-week

### Dataset
XAUUSD M30, 50,000 bars, 2022-05-03 → 2026-07-24 (**4.24 y**), 1,044–1,054
usable days per specification.

### Gate
Any predictor significant after multiple-testing correction; specifically the
paper's r₁ → r_last relationship.

### Three headline numbers
- **0 of 12** predictors reach |t| > 1.96 on the US-session analogue
- The paper's headline predictor r₁: β = 0.0063, **t = 0.27**, R² = **0.015%**
- Gold's own day: 3 of 45 significant vs **2.2 expected by chance**; **zero**
  survive Bonferroni (threshold |t| = 3.25)

### Mechanism of failure
The effect is simply absent. It is not obscured or marginal — the flagship
predictor's R² is 0.015%, i.e. the first half-hour explains fifteen
ten-thousandths of one percent of the closing half-hour's variance.

Subgroup cells producing |t| > 2 (hi-vol tercile, Thursday/Friday) are single
cells among ~180 subgroup tests with n ≈ 210 each, and they **flip sign between
adjacent buckets** — the signature of noise, not structure.

### Structural finding worth carrying forward
**The paper's design does not map onto gold.** In the original, the last
half-hour is the US equity close — the most liquid bucket of the day. Gold's
final pre-halt bucket (20:30 GMT) is its **quietest**: 5.6 bps mean absolute
move versus 20.0 bps at the US open. Applying an equity-microstructure
specification to a 23-hour OTC market requires this check first.

### Data/measurement limitations
- Server→GMT offset had to be **calibrated from data**, not assumed. The MT5
  tick was stale (weekend), so the clock difference method was useless. Matching
  the broker's mean |30-min return| profile against independently-placed
  HistData M1 gave **corr 0.9998 at −3h** versus 0.38 at the next-best shift.
  Broker = EET/EEST; GMT = server − 3 (summer) / − 2 (winter).
- Gold's day is **46** half-hour buckets, not 48: server 00:00–01:00 has **zero**
  bars (COMEX 17:00 ET halt = 21:00–22:00 GMT summer). An initial diagnostic
  missed this because `nsmallest()` cannot show slots whose bar count is zero —
  they are absent from the groupby index entirely.
- Session-anchored events smear ±1 h across the DST boundary when aggregated by
  true GMT. Validated by checking that the 08:30 ET release spike lands at 12:30
  GMT in summer and 13:30 GMT in winter, and that the ET-anchored profile is
  stable (loudest 09:30 / 10:00 / 08:30 ET).

---

## Family 3 — Autocorrelation reversion

### Specification
Lag-1 autocorrelation of log returns at every tradeable timeframe, plus
per-GMT-hour conditional autocorrelation `corr(r_h, r_{h+1})`.

### Datasets
- XAUUSD M30 and H1 from broker: 4.2 y / 8.5 y
- XAUUSD M1/M5/M15/M30 from HistData files: 2026-04-01 → 2026-06-26 (3 months)

### Gate
ρ₁ materially different from zero, and large enough to exceed cost.

### Three headline numbers

| timeframe | ρ₁ | t |
|---|---|---|
| M1 | −0.0365 | **−10.6** |
| M5 | −0.0042 | −0.5 |
| M15 | −0.0158 | −1.2 |
| M30 | −0.0078 | −0.4 |
| H1 | −0.0037 | −0.1 |

- ρ₁ ≈ **0** at every timeframe from M5 to H1 (all |t| < 1.4)
- The only significant reading (M1) is **bid-ask bounce**: mean |M1 move| is
  2.9 bps, so ρ = −0.037 implies ~**0.1 bps** of theoretical edge against a
  **1.06 bps** round trip — untradeable by ~10×
- Per-hour: reversal outnumbers momentum **11 : 3**, but |ρ| is only 0.06–0.15

### Mechanism of failure
There is no exploitable serial dependence. The answer to "positive means
momentum works, negative means reversion works" is **neither** — the coefficient
is indistinguishable from zero wherever it could be traded, and the one place it
is measurable is a microstructure artifact smaller than the spread.

### Cost context (measured, and the one thing that looked survivable)
Regime-adjusted round-trip cost as a fraction of the average move:

| timeframe | typical mean move | cost / move |
|---|---|---|
| M5 | 4.2 bps | 25.2% |
| M15 | 7.4 bps | 14.3% |
| M30 | 10.3 bps | 10.3% |
| H1 | 14.9 bps | 7.1% |

M15 and slower clear a 20% cost threshold — but with ρ₁ ≈ 0 there is nothing to
spend that headroom on. **These families died on signal, not cost**; better
spread data would not rescue them.

### Data/measurement limitations
- **A naive M15-vs-M30 comparison across the two sources is invalid.** The
  HistData window (Apr–Jun 2026) is **1.50× more volatile** than the 4.2-year
  average, so fast timeframes appear artificially cheap. All rows in the cost
  ladder were recomputed on one window, with a regime-adjusted column.
- `cost / average move` is a **floor** on cost burden. A strategy capturing only
  a third of the average move faces roughly 3× these figures.
- No per-bar spread history exists, so one constant spread is applied across all
  24 hours. Every hourly cost figure is a **lower bound in thin hours** — worst
  in the 20:00–21:00 GMT pre-halt window, which is both gold's quietest bucket
  and the literature's target half-hour.

---

## Family 4 — Value-area migration continuation

### Specification
- Build prior-session volume profile: bins of 1 bp of price; each bar's
  tick volume spread uniformly across the bins its high-low spans; POC = max
  volume bin; value area expanded from POC taking the richer neighbour until
  70% of session volume → VAH/VAL
- Signal: first breach per session per side of the **prior** session's VAH
  (long) or VAL (short) — continuation, not reversion
- Volume filter: `vratio` = mean tick volume of last 3 bars / mean of prior 20.
  **Q4-rising** (vratio ≥ 1.5) primary; **Q1-quiet** (vratio ≤ 0.35) secondary
- Entry at breach-bar close; **stop = opposite side of the breach bar, or 2×ATR,
  whichever is tighter**; targets 1.5R and 2R; 12-hour time exit
- One position at a time, 1% risk, pessimistic intrabar

### Datasets
| symbol | tf | raw | **volume-usable** |
|---|---|---|---|
| EURUSD | M15 | 29,606 bars / 1.19 y | 29,606 / **1.19 y** (0% zero) |
| EURUSD | M30 | 14,804 / 1.19 y | 14,804 / **1.19 y** (0% zero) |
| XAUUSD | M15 | 50,000 / 2.12 y | 14,348 / **0.61 y** (71% zero) |
| XAUUSD | M30 | 50,000 / 4.24 y | 7,175 / **0.61 y** (86% zero) |

EURUSD primary; XAUUSD flagged **THIN** and not weighted equally.

### Gate
win rate > 50% **and** RETURN/DD > 1.5 **and** max DD < 10%.

### Three headline numbers
- **0 of 16 cells passed; 0 of 8 EURUSD cells**
- Win rates **29.6–42.9%**; all eight EURUSD variants **lost money**
  (−14.8% to −37.0%)
- RETURN/DD **0.70–0.82**, max DD **17.6–39.4%**, challenge pass rate **0%**

### Mechanism of failure
**Excursion ratios measured without a stop do not survive stop placement.**

The descriptive pass looked supportive: for a continuation trade the excursion
roles invert, so the measured median MFE/MAE of 0.37–0.44 became **2.3–2.7 in
favour of continuation**. But that was measured *from the level with no stop*.

Once the stop was imposed, median **MAE was 5–6 R** on the Q1 cells — normal
noise routinely travelled several multiples of the stop distance against the
position. Price stopped out **59–70%** of the time *before* the migration
developed. The "opposite side of the breach bar, whichever tighter" rule
produces an R so small relative to instrument noise that sequencing beats
magnitude: **the adverse leg arrives first.**

Halving the assumed spread did not rescue it (best cell still −24.1%, RETURN/DD
0.57), so cost was not the binding constraint. Stability was poor — EURUSD M30
Q1 1.5R went 49% → 29% win rate across halves.

### Descriptive findings (prerequisite study)
- **Revert-to-POC rate is remarkably stable**: ~50% within 12 h across two
  unrelated instruments and both timeframes (XAUUSD 51.4%/52.7%; EURUSD
  50.6%/50.4%), median 2.0–3.2 h.
- **Median MFE/MAE 0.37–0.44 at 4 h**, always < 1.0 at every horizon on every
  series. Price moved 2–3× further *away* from value than back toward it.
- **The "tired auction" hypothesis is contradicted.** Extensions on *falling*
  volume reverted **less**, not more. Raw EURUSD showed −25.6 pp (z = −5.7), but
  that was substantially a **session-open artifact**: a breach at the open
  mechanically has low volume versus the prior 20 bars from the previous
  session's active hours, and 100% of open-hour events were labelled "tired"
  while reverting only 12.7%. Controlling by removing the modal hour, the effect
  **survived in 4 of 8 cells** (EURUSD M15: −19.2 pp, z = −3.37) and the sign
  was negative in **all eight**. Q4-loudest reverted 40–47% vs Q1-quietest
  12–21%.

### Data/measurement limitations
- **Tick volume is a proxy**, not traded contracts. For OTC gold/FX there is no
  consolidated tape, so the value area approximates *where business was done*.
- **Intra-bar volume shape is assumed** uniform across the high-low span.
- **Gold's usable volume history is one regime of 0.61 years** — see
  [MT5 data landmines](#mt5-data-landmines).
- **Hour-of-day is largely unanswerable with this design.** Taking the first
  extension per session concentrates 26–30% of events in a single hour, leaving
  most hours with n < 15. The apparent "hour effect" is mostly *time since
  session open*. Answering it properly needs an all-fresh-breaches definition.

---

## Family 5 — Cointegration pair trading

### Specification
- Pairs: XAUUSD/XAGUSD, US30/US500, US500/US100, US30/US100
- Rolling hedge ratio: OLS of **log price** on log price within each window
- Engle-Granger: ADF on the cointegrating regression's residuals, no constant
  (residuals are mean-zero by construction)
- Windows: 60 d and 120 d (M30/H1); **250 and 500 trading days** (D1);
  step 10 days
- Half-life from AR(1): `−ln2 / ln(1+φ)`
- Two-leg round-trip cost: `2·hs_A + 2·|β|·hs_B`

### Datasets
| tf | span | windows/pair |
|---|---|---|
| M30 | 4.23–4.24 y | 97–103 |
| H1 | 8.45–8.54 y | 204–212 |
| D1 | 11.9–21.6 y (2,812–5,621 bars) | 501–526 |

### Gate (D1, pre-committed)
Cointegration holding in a **majority** of windows at 5% **and** median EG
statistic **< −3.34** **and** half-life **< ~20 days**.

### Three headline numbers
- **Hold@5% = 1.0–9.0%** across all timeframes — at or **below** the 5% expected
  by chance under the null
- **Median EG statistic −1.49 to −2.75**, never near the −3.34 threshold
- Half-life **17–37 trading days** at D1; only 3 of 8 D1 cells under 20 d, and
  those fail worst on the other two conditions

**0 of 8 D1 cells passed all three.**

### Mechanism of failure
**The relationship is not hidden — it is not there.** D1 over 250/500-day
windows was the specific test of whether a slow equilibrium was being drowned in
intraday noise. It was not: D1 hold@5% (3.5–8.4%) essentially matched M30/H1
(1.0–9.0%), and median EG t moved only from ~−2.0 to ~−2.2. Same-sector pairs
with 20 years of history is the friendliest possible case.

Hedge ratios were sign-stable (97–100%) but drifted ~2× in magnitude across
windows (US30/US100 β spanned 0.296–0.639), so even the hedge was unstable.

Longer windows were generally *worse* than shorter ones — the opposite of what a
genuine long-run equilibrium too subtle for 60 days would show.

### The one favourable number
**Cost is negligible: 0.3–1.4% of the spread's own 2σ move.** Spreads would need
to be **14–45× wider** before cost reached 20% of 2σ. 2σ moves are 136–762 bps
against a 1.4–4.0 bps round trip. This is the inverse of the intraday problem:
**cheap to trade, nothing to trade.**

### Methodological notes that mattered for correctness
- **Engle-Granger residuals require cointegration critical values, not plain ADF
  ones.** Because β is estimated, the null distribution shifts left; using
  standard ADF values (−2.86 at 5%) would over-reject badly. Published
  asymptotics for one regressor plus constant: **−3.90 / −3.34 / −3.04**.
- scipy/statsmodels are unavailable, so p-values came from **Monte Carlo** under
  the null (two independent random walks through the identical pipeline).
  Validated: simulated quantiles matched published asymptotics to **±0.03** at
  T = 500 / 1000 / 2500. ADF separated AR(0.95) at t = −8.32 from a random walk
  at t = −1.55. Half-life estimator bias −7% to +3% over 300 reps, but
  high-variance near the unit root — hence medians across windows.
- **Rolling windows overlap heavily** (60 d stepped 10 d = 83% overlap). "Hold
  fraction" is a share of **calendar time**, not independent confirmations. This
  makes the low fractions *more* damning, not less: no sustained cointegrated
  epoch appears anywhere.

### Data/measurement limitations
Cost uses a **closed-market spread snapshot** (`symbol_info` last-known bid/ask;
`symbol_info_tick` returned zeros for silver and all three indices). Given
14–45× headroom, even a 3× understatement would not change the conclusion.

---

## Family 6 — Avellaneda-Lee residual reversion

### Specification
Avellaneda & Lee (2010), *Statistical Arbitrage in the US Equities Market*.
**Not cointegration** — returns-on-returns, cumulative residual, OU-fitted
s-score.

Per bar, rolling 60-day window:
1. OLS `r_A = α + β·r_B + ε` (**returns**, not log prices)
2. `X_t = cumsum(ε)` within the window (resets each window, as in the paper)
3. OU fit via AR(1) on X: `X_{t+1} = a + b·X_t + ζ`
   - `κ = −ln(b)`, `m = a/(1−b)`, `σ_eq = √(var(ζ)/(1−b²))`
   - `s = (X_t − m)/σ_eq`, half-life `= ln2/κ`
4. Entry |s| > 2, exit |s| < 0.5, hard stop |s| > 3.5, time stop 20 days.
   s < −2 → long the spread.

Refit daily; between refits β/m/σ_eq held and X updated bar-by-bar so stops are
evaluated at full bar resolution. Sizing 1% of current equity to the |s| = 3.5
stop.

### Datasets
Same four pairs, D1 (11.9–21.6 y) and H1 (8.45–8.54 y). 1,849–5,540 windows per
pair.

### Gate
win rate > 50% **and** RETURN/DD > 1.5 **and** max DD < 10%.

### Three headline numbers
- **0 of 8 cells passed**
- Best RETURN/DD **0.11** — off the 1.5 gate by more than 13×
- Two cells cleared win rate (US500/US100 D1 **59.0%**, H1 53.5%) and one
  cleared max DD (−8.3%), but never together, and the 59% rests on **39 trades
  over 12 years**

| pair / tf | trades | win% | ret% | maxDD% | RET/DD | HL days | stationary |
|---|---|---|---|---|---|---|---|
| XAUUSD/XAGUSD D1 | 78 | 34.6% | −13.5% | −15.8% | 0.04 | 5.2 | 6.5% |
| US30/US500 D1 | 68 | 44.1% | +23.0% | −13.9% | 0.07 | 5.8 | 4.1% |
| US500/US100 D1 | 39 | 59.0% | +7.2% | −8.3% | 0.07 | 5.3 | 5.8% |
| US500/US100 H1 | 114 | 53.5% | −10.4% | −29.5% | 0.04 | 4.5 | 6.0% |

### Mechanism of failure
The specification *does* behave better than cointegration — exit mixes are
roughly balanced (64 targets vs 52 stops on US30/US500 H1) and win rates sit
near the ~50% a symmetric ±1.5σ structure implies. But returns are
flat-to-negative with RETURN/DD 0.04–0.11: **no exploitable edge**, and a
plausible share of even the flat performance is the mechanical artifact the null
control exposed.

### Two calibration findings that bound how these results can be read
1. **The stationarity metric is nearly powerless at W = 60.** On a synthetic
   pair whose residual is a **known OU process**, the 60-day ADF flagged
   stationarity only **9.6%** of the time. The real pairs show 4.1–7.3%. Those
   low fractions therefore do **not** demonstrate absence of mean reversion.
   Relatedly the OU fit is biased low on 60 points (recovered HL 5.2 d vs a true
   11.6 d), so the ~5-day half-lives above are likely understated.
2. **The null control was profitable.** A random-walk residual produced **+6.5%
   and a 52.4% win rate** purely from the rolling-window reset plus small-sample
   Dickey-Fuller bias (which forces b < 1, so the fit almost always *reports*
   mean reversion — hence the A-L κ filter passing 96.8–100% of windows).
   Any positive result here must beat a noise benchmark, not zero.

Monte Carlo null for ADF-on-cumulative-residual: 5% critical value **−2.59 to
−2.66** — neither the standard no-constant ADF value (−1.95) nor Engle-Granger's
(−3.34), confirming this construction needs its own simulated null.

### Data/measurement limitations
**The execution assumption dominates the signal question.** The intrabar
bracket is very wide — US30/US500 D1 spans **+23.0% to −71.5%** between
close-based and worst-case-bound stops. Settling any pair strategy properly
requires tick or M1 data on **both legs** to observe real joint intrabar paths;
bar data on two instruments cannot do it.

---

## Family 7 — 09:00 GMT gold reversal

### Specification
- **Provenance:** the per-hour autocorrelation study found 08:00 GMT momentum
  (t = +4.33) and 09:00 GMT reversal (t = −4.35), both surviving Bonferroni
  across 24 hours. That correction is spent, so **09:00 only was tested and no
  other hour was searched.**
- Signal: the 08:00 GMT bar closed "strongly directional" —
  **single pre-committed, untuned definition: |close − open| ≥ 0.50 × (high − low)**
- Entry: **opposite** that direction, at the 09:00 GMT bar's **open**
- Stop and target: fixed multiples of the 08:00 bar's **range**; variants
  0.5×/1.0× and 1.0×/1.0× (stop/target)
- Time exit: end of the 09:00 bar, and +4 hours — tested **separately**
- Pessimistic intrabar (stop fills if both levels lie inside a bar); 1% risk to
  the stop; gold half-spread 0.55 bps charged on entry and exit

### Dataset
XAUUSD H1, 50,000 bars, 2018-01-14 → 2026-07-30 (**8.54 y**). 2,182 08:00 GMT
bars; **888 qualifying signals** (40.7%); 457 up / 431 down. 104 trades/year.

### Gate
win rate > 50% **and** RETURN/DD > 1.5 **and** max DD < 10%.

### Three headline numbers
- **0 of 4 variants passed**; win rates **33.1–48.4%**
- Expectancy **negative in all four**: −0.049 R to −0.138 R
- **Null control p = 0.28–0.58** — indistinguishable from random direction

| variant | trades | win% | exp R | ret% | maxDD% | RET/DD |
|---|---|---|---|---|---|---|
| 0.5× / 1.0×, 1 h | 888 | 42.8% | −0.108 | −63.0% | −65.3% | 0.17 |
| 0.5× / 1.0×, 4 h | 888 | 33.1% | −0.138 | −72.8% | −75.2% | 0.19 |
| 1.0× / 1.0×, 1 h | 888 | 46.7% | −0.049 | −36.2% | −39.7% | 0.13 |
| 1.0× / 1.0×, 4 h | 888 | 48.4% | −0.054 | −40.3% | −46.6% | 0.13 |

### Null control (the decisive result)
200-run permutation test: the observed vector of 08:00 signs is shuffled
(preserving its up/down mix) and identical rules re-run, holding dates, ranges
and all subsequent price action fixed.

| variant | real win% / null | real expR / null | p(win) | p(expR) |
|---|---|---|---|---|
| 1.0× / 1.0×, 4 h | 48.4% / **48.1%** | −0.054 / **−0.055** | 0.460 | 0.475 |
| 1.0× / 1.0×, 1 h | 46.7% / 45.7% | −0.049 / −0.047 | 0.280 | 0.545 |
| 0.5× / 1.0×, 1 h | 42.8% / 41.8% | −0.108 / −0.110 | 0.285 | 0.440 |

Differences are ~1 pp on win rate and ~0.002 R on expectancy. **The 08:00
direction carries no tradable information.**

### Split-half stability
Both halves negative in every variant (1.0×/1 h: 46.5% → 47.0%; expectancy
−0.058 → −0.040). Best single cell is second-half 4 h at 51.2% win / −0.011 R —
still unprofitable. **No regime where it works.**

### Mechanism of failure
The edge is smaller than the transaction cost — see
[Lesson 1](#lesson-1--screen-edge-in-r-against-cost-in-r-before-writing-a-backtest)
for the full arithmetic. Gross edge ≈ 0.064 R against cost of 0.042 R (1.0×) and
0.084 R (0.5×); observed net expectancy matches cost almost exactly, so **gross
edge ≈ zero**.

This died *earlier* than Families 4 and 6. Those failed from stop placement
interacting with sequencing; here **no stop placement could have rescued it**,
because the signal's economic content is below the spread.

---

## FTMO rule map (paraphrased)

My own summary of the rules, written after asking FTMO support on
**2026-07-29**. Nothing below is a quotation, and rules change, so check with
FTMO before relying on any of it. These points were previously *assumptions*
in `ftmo_portfolio_sim.py` / `ftmo_overlay.py`; the answers matched them, so
the simulations that used them were not re-run.

| Rule | My understanding (paraphrased) | Impact on our work |
|---|---|---|
| Drawdown basis | **Equity-based, including floating P&L** on open positions. Balance is irrelevant; an open position in temporary drawdown *can* breach the account. | This was our pessimistic assumption and it is **correct**. The ~40% FAIL_DAILY rate was real, not a modelling artifact. **No re-run warranted.** |
| Max Loss (2-Step) | **10% of the initial balance, STATIC.** EoD-trailing applies to the 1-Step, not 2-Step. | Simulator was right. |
| Day rollover | **Midnight Prague** (CET UTC+1 winter / CEST UTC+2 summer). | `ftmo_overlay.py` was fixed to be DST-aware (`to_prague()` / `INDEX_TZ`). MT5 server time (EET/EEST) is a **constant −1 h** from Prague because both shift on the same EU dates. |
| Hedging within one account | **Permitted, no general restrictions.** Buying and selling the same or correlated instruments as a deliberate strategy is fine. | Clears two-leg spread trades on 2-Step. |
| Cross-account hedging | **Strictly prohibited** — opposite positions across different accounts, *any* firm or broker. Risks termination. | **Concrete exposure:** two logins exist (<redacted> dead, <redacted> live) and the bot pointed at the stale one for nine days. Keep bot init as bare `mt5.initialize()` so it can only ever act on the single logged-in account. |
| 1-Step restriction | **Forbids** using hedging on same/highly-correlated instruments to lock profit and close later (circumvents the Best Day Rule). | A gold/silver or index spread is *literally* that structure. **Permanent reason to stay on 2-Step.** |
| Rewards Standard | Must close positions when a market is shut > 2 h → Mon–Fri holds, flat over weekends. | Fragments multi-day spread positions. |
| Rewards Swing | **No weekend or news restriction.** | Prefer Swing for anything with a multi-day holding period. |
| Retries | Unlimited and free. | — |

### Rule interaction worth remembering
Equity-based accounting **punishes directional trades** (one leg, wide stop, full
floating swing hits the daily limit) but is **structurally mild toward a hedged
pair** — when both legs fall, one leg's loss offsets the other's gain, so net
floating exposure is the *spread's* movement, not either instrument's. This is
why spread volatility measured 74–256 bps while the underlying instruments move
multiples of that. The rule that killed the directional families is comparatively
gentle on a market-neutral spread. *(That insight survives even though none of
the tested pairs actually cointegrated.)*

---

## MT5 data landmines

Each of these silently corrupts an analysis rather than raising an error.

### 1. XAUUSD `tick_volume` is ZERO before 2025-12-17
The broker back-filled years of price history with no volume: **71% of M15 bars
and 86% of M30 bars** carry `tick_volume = 0`. Any volume-profile / VWAP /
value-area work on gold has only **~0.61 usable years**, not the 2.1–4.2 years
the bar count advertises.

**This inverts the obvious expectation:** EURUSD is 100% populated over its full
1.19 years, so the *shallower* symbol has the **deeper usable volume history**.
Always trim to the volume-populated window and report what was dropped — an
early run silently analysed only the 157 volume-bearing gold sessions out of 547.

### 2. EURUSD daily history is capped at ~306 bars
Confirmed on this account. **However, this cap does not generalise** — D1 for
XAUUSD/XAGUSD/US30/US500 returned 2,812–5,621 bars (11.9–21.6 years). Always
probe per symbol rather than assuming a global limit.

### 3. No per-bar spread history
The terminal journal reports `49 symbols, 0 spreads`. Any intraday cost model
must apply one constant spread and is therefore a **lower bound in thin hours** —
worst exactly in the pre-halt window that intraday literature targets.

### 4. Bar requests above ~50,000 return `None`, not a truncated array
Not an error — an empty frame, producing a confusing `KeyError: 'time'`
downstream. Always fall back through descending sizes.

**Corollary that cost real work:** history depth depends on the request size. An
early Donchian run asked for 6,000 H4 bars and got 3.9 years; asking for 50,000
returned **20.4 years**. Conclusions were drawn on 19% of available data, and the
3.9-year window happened to be a gold bull run.

### 5. Weekend/closed-market ticks are stale and spreads are widened
A Friday-close snapshot read gold half-spread **0.0134%** versus the stated
0.0053% — **2.5×**. Never calibrate cost from a weekend tick. Also: a stale tick
makes the clock-difference method for deriving the server timezone useless (the
tick read Fri 23:58 server while true UTC was Sat 13:22).

### 6. `symbol_info_tick` can return zeros while `symbol_info` still has bid/ask
Silver and all three indices returned `bid = ask = 0` with epoch-zero timestamps
when their markets were closed, but `symbol_info` carried usable last-known
bid/ask. Use the latter as a fallback, flagged as a closed-market snapshot.

### 7. `.env.mt5` credentials go stale
**Observed 2026-07-25:** the file listed login `<redacted>` while the terminal
ran `<redacted>` (OANDA demo accounts rotate). Explicit-login init failed with
`-6 Authorization failed`; bare `mt5.initialize()` attached to whatever the
terminal had open and worked.

**Corrected 2026-08-06:** `.env.mt5` now reads `<redacted>` and matches the live
account. Keep it that way — and note that bare `mt5.initialize()` remains the
safer call regardless, because it can only ever act on the single logged-in
account, which is the mitigation for the FTMO cross-account hedging rule.

**Separate failure mode, same error code:** `-6 Authorization failed` can also
occur with *correct* credentials on a freshly restarted terminal, while the
terminal itself is visibly logged in and charting. Distinguish it from
`-10004 No IPC connection`: `-6` means the terminal was reached but rejected the
handshake (usually algorithmic trading / API access disabled in the terminal, or
an elevation mismatch between terminal64.exe and the Python process). `-10004`
means the terminal could not be reached at all.

### 8. Server clock is EET/EEST, calibrated not assumed
GMT = server − 3 (summer) / − 2 (winter), established by matching the broker's
mean |30-min return| profile against independently-placed HistData M1:
**corr 0.9998** at −3 h vs 0.38 at the next-best shift. Gold's day is **46**
half-hour buckets; server 00:00–01:00 has **zero** bars (COMEX 17:00 ET halt).

### 9. `nsmallest()` cannot reveal zero-count groups
A slot with zero bars is absent from the groupby index entirely, so it never
appears in a "lowest counts" listing. This caused an initial false conclusion
that gold's M30 series had *no* daily halt. Check for missing keys explicitly.

---

## Engine bugs found and fixed

Documented because each produced **plausible-looking but wrong** output. In three
cases a synthetic control caught the bug; in one case only an arithmetic
sanity-check did.

### 1. Avellaneda-Lee: P&L read off a resetting series
`X` resets at every refit, so `X_exit − X_entry` compared cumulative residuals
from **different windows** — meaningless. Caught by a synthetic pair with a known
OU residual returning **14 target exits and a 0% win rate**, which is
arithmetically impossible. Fixed by accruing realised spread return under the
hedge ratio fixed at entry.

### 2. Avellaneda-Lee: "pessimistic intrabar" fabricating losses
Pairing leg A's *low* with leg B's *high* on the same bar assumes ~0.8-correlated
legs move in **opposite** directions intrabar, every bar. This manufactured an
adverse excursion continuously and produced win rates of **1.6–20.7%** — the
artifact, not the market (a symmetric ±1.5σ structure must give ~50%). Since the
joint intrabar path of two legs is unobservable from bar data, close-based stops
became primary (stop still wins ties) with the extreme version retained as an
explicit **bracket**.

### 3. Risk architecture: uncapped challenge duration inflating pass rates
With 20 years of data, an attempt starting in 2006 could run for years before
reaching +15.5% compounded. This produced an apparent **70% pass rate** and a
**99.7% "conditional" pass rate** that were pure artifacts. Caught by the
`bars2pass` column: 11,903 H4 bars = **7.5 years**. Fixed by adding realistic
time caps (3/6/12/24 months), which showed **0 of 12 configs reach 60% at any
horizon**.

### 4. `ftmo_overlay.py`: DST-naive day boundary
`TZ_OFFSET_HOURS = 2` was fixed, so correct for only half the year — and a wrong
day boundary manufactures phantom daily-loss breaches. Replaced with DST-aware
`to_prague()` plus an `INDEX_TZ` switch. Validated: server-time indices shift a
constant −1 h (correct on both sides of the October boundary since EET and
Prague move together); UTC indices get +1 h winter / +2 h summer with the March
boundary handled.

### 5. Malformed ternary and entry-bar mark-to-market
A chained conditional in the day-rollover line parsed correctly only by accident,
and the entry-bar MTM ignored the half-spread already embedded in the entry
price. Both fixed before the sweep ran.

### Validation practices that earned their place
- **Synthetic controls with known answers** before every real run (known-OU
  residual, known-κ process, hand-built bars for trade mechanics).
- **Null / permutation controls** — decisive in Families 6 and 7.
- **Monte Carlo critical values validated against published asymptotics**
  (matched to ±0.03).
- **Hand-computed test cases** for the FTMO challenge engine. Two initial
  "failures" turned out to be *my* arithmetic errors (forgetting the 1.10 × 1.05
  = +15.5% compounding, and the phase-2 min-days reset), not code errors — worth
  recording as a reminder to verify the expectation before editing the code.

---

## File manifest

### `research/runs/` — captured console output
| file | family | note |
|---|---|---|
| `01b_risk_architecture_UNCAPPED_superseded.txt` | 1 | **superseded** — uncapped duration artifact (bug 3) |
| `01c_risk_architecture_timecapped.txt` | 1 | final, with 3/6/12/24-month caps |
| `02_03_intraday_momentum_and_autocorrelation.txt` | 2, 3 | Gao/Han/Li/Zhou tables + autocorr + cost ladder |
| `04a_volume_profile_descriptive.txt` | 4 | VAH/VAL/POC, revert rates, tired-auction control |
| `04b_value_area_migration_backtest.txt` | 4 | 16-cell backtest |
| `05a_cointegration_M30_H1.txt` | 5 | intraday windows |
| `05b_cointegration_D1.txt` | 5 | 250/500 trading-day windows |
| `06a_avellaneda_lee_BUGGY_superseded.txt` | 6 | **superseded** — bugs 1 and 2 visible here |
| `06b_avellaneda_lee_final.txt` | 6 | final, with intrabar bracket |
| `07_gold_0900_reversal.txt` | 7 | + permutation null + split-half |

### `research/equity_curves/` — 15 CSVs
Donchian portfolio equity curves: Universe A/B baselines, MAX_OPEN 2 vs 3,
market vs limit entry, and the breadth runs (`long_3sym`, `long_7sym`,
`long_7sym_adx`, `short_full`, `short_full_adx`).

### `research/data/` — HistData XAUUSD M1
`DAT_MT_XAUUSD_M1_2026{04,05,06}.csv` + status `.txt` files.
2026-04-01 → 2026-06-26, 84,796 rows. File clock is **ET with DST (EDT)** — the
17:00 hour is entirely absent (COMEX halt), so **+4 h = GMT** for this period.
Used for the M1/M5/M15 cost ladder and to calibrate the broker's server offset.
Original zips are not included in this repository.

### Scripts (in `scripts/`)
`ftmo_portfolio_sim.py` · `ftmo_overlay.py` · `gold_intraday_probe.py` ·
`gold_calibrate.py` · `gold_intraday_stats.py` · `volume_profile_research.py` ·
`va_migration_test.py` · `coint_research.py` · `coint_d1.py` ·
`avellaneda_lee.py` · `gold_risk_architecture.py` · `gold_0900_reversal.py`

---

## If this is picked up again

The single highest-value change to the process: **apply the edge-in-R vs
cost-in-R screen before writing any backtest.** It would have closed several of
these families in an afternoon rather than a day of engine work each.

The two open threads, both honestly scoped:
1. **A broad cointegration screen** — whether *any* instrument pair on this
   broker cointegrates, rather than testing four hand-picked same-sector pairs.
2. **Tick or M1 data on both legs** — the only way to settle a pair strategy,
   because the intrabar execution bracket (+23% to −71% on one cell) currently
   dominates the signal question.

What is **not** worth revisiting: parameter tweaks on any of the seven families
above. Each has a measured mechanism of failure, not an unlucky configuration.
