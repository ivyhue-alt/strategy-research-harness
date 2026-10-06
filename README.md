# strategy-research-harness

Research only. No profitability claim.

This is the record of a backtesting project that ended in a decision to stop.
I tested a Donchian trend-following strategy against a prop-firm challenge
(FTMO 2-Step), wrote the pass/fail gate down before each run, and the strategy
failed its gates. I then tested six more strategy families the same way. None
passed. The full write-up is in [research/FINDINGS.md](research/FINDINGS.md).

Nothing here is trading advice, and nothing here made money.

## What is in this repository

| path | contents |
|---|---|
| `research/FINDINGS.md` | The full write-up: specification, gate, result and mechanism of failure for each family |
| `research/runs/` | Captured console output from each run, including two superseded runs that show bugs before they were fixed |
| `research/equity_curves/` | 15 CSVs of Donchian portfolio equity curves |
| `research/data/` | HistData status reports for the M1 gold files (the data files themselves are not included, see below) |
| `scripts/` | The 12 Python scripts that produced the results (see [Scripts](#scripts)) |

Broker account logins and local file paths were removed from FINDINGS.md, the
run logs and the scripts. The FTMO rule section in FINDINGS.md is my own
paraphrase, not a quotation.

## What the harness does

- **Parameter sweeps.** The signal is held fixed while one thing is varied at a
  time. For example, the risk-architecture test swept risk per trade
  (0.25–1.00%) against a daily circuit breaker (none / flatten at −2% / −3%),
  with 532 challenge starts per config.
- **Prop-firm challenge overlay.** Each backtest is replayed against the FTMO
  2-Step rules from rolling start dates, with time caps of 3, 6, 12 and 24
  months, to get a pass rate rather than a single equity curve.
- **Risk-adjusted metrics and drawdown.** RETURN/DD, CAGR/DD, max drawdown,
  win rate and expectancy in R. Drawdown is equity-based and includes floating
  P&L. Sizing is fixed-fractional off current equity.
- **Monte Carlo and null controls.** Critical values for the cointegration
  tests come from Monte Carlo simulation under the null (two independent random
  walks run through the identical pipeline), checked against published
  asymptotics to ±0.03. Two families were also run against a null control (a
  200-run permutation test for one, a random-walk residual for the other) so
  that a result has to beat noise, not zero.
- **Pessimistic fills.** If the stop and the target both sit inside one bar,
  the stop wins.

## The strategy family: Donchian trend following

- Entry: close breaks the 20-period Donchian channel. Exit: 55-period opposite
  channel.
- Filter: ADX ≥ 20 (Wilder, 14)
- Stop: 2 × ATR(14)
- Risk: 1% of current equity, max 3 concurrent positions
- Timeframe: H4, market entry at the breakout-bar close

Tested on XAUUSD, US100 and BTCUSD (3.9 years), then on 7 symbols (3.8 years),
then on XAUUSD alone over 20.4 years.

## The gates, written down before each run

| test | gate |
|---|---|
| Breadth (3 symbols → 7 symbols) | RETURN/DD > 1.5 |
| Risk architecture (gold, 20.4 years) | 60% FTMO 2-Step pass rate |

## The result

**Breadth test.** Gate: RETURN/DD > 1.5.

| config | RETURN/DD | max DD | pass rate |
|---|---|---|---|
| 3-symbol baseline | 0.95 | −26.0% | 39% |
| 7-sym, list order | 0.09 | −45.4% | 10% |
| 7-sym, ADX tie-break | 0.03 | −44.3% | 15% |
| 7-sym, 4 random orders | 0.02–0.25 | −33.1 to −40.8% | 15–18% |

Adding instruments made it worse. The book was 79% slot-saturated at 3
concurrent positions, so extra symbols filled the same three slots with weaker
signals. This was not a concentration problem: effective breadth measured
6.27–6.89 of 7 nominal. Diversification happened and still lost.

**Risk architecture test.** Gate: 60% pass rate.

| config | 3 mo | 6 mo | 12 mo | 24 mo |
|---|---|---|---|---|
| 0.25% risk | 0.0% | 0.0% | 0.0% | 0.0% |
| 0.75% / −2% | 3.4% | 10.2% | 29.5% | 56.6% |
| 1.00% / −2% | 5.1% | 13.7% | 34.8% | 54.9% |

0 of 12 configs reach 60% at any horizon. The best 12-month pass rate is 34.8%
and the best 24-month pass rate is 56.6%.

**Why sizing cannot fix it.** Over 20.4 years, gold-alone Donchian H4 returns
+150% total: CAGR 4.61%, max DD −26.9%, CAGR/DD 0.17. Passing a challenge in 12
months needs about 16% annualised, which at CAGR/DD 0.17 implies a drawdown of
about 91%. FTMO permits 10%. The required drawdown exceeds the permitted
drawdown by 9–20×, and position sizing scales return and drawdown together.

**Decision: the family is closed.** An earlier 3.9-year window had looked
better (+151%, RETURN/DD 1.04), but that window was a gold bull run. It was
19% of the data available.

## The other six families

Tested the same way after Donchian was closed. Summary table from FINDINGS.md:

| # | Family | Instruments / TF | Span | Gate | Result | Verdict |
|---|---|---|---|---|---|---|
| 1 | Donchian trend | gold/US100/BTC → 7 sym, H4 | 3.8–20.4 y | RETURN/DD > 1.5 | 0.03–0.25 (breadth); 0.17 (gold 20 y) | closed |
| 2 | Intraday momentum | XAUUSD M30 | 4.2 y | any predictor significant | 0 of 12 on clean analogue | closed |
| 3 | Autocorr reversion | XAUUSD M1–H1 | 3 mo–8.5 y | ρ₁ materially ≠ 0 | ρ₁ ≈ 0 at M5–H1 | closed |
| 4 | Value-area migration | EURUSD M15/M30 | 1.19 y | win>50%, R/DD>1.5, DD<10% | 0 of 16 cells | closed |
| 5 | Cointegration pairs | 4 pairs, M30/H1/D1 | 1.2–21.6 y | majority hold@5%, EG t<−3.34, HL<20 d | 1.0–9.0% hold | closed |
| 6 | Avellaneda-Lee | 4 pairs, D1/H1 | 8.5–21.6 y | win>50%, R/DD>1.5, DD<10% | 0 of 8; best R/DD 0.11 | closed |
| 7 | 09:00 GMT reversal | XAUUSD H1 | 8.54 y | win>50%, R/DD>1.5, DD<10% | 0 of 4; null p=0.28–0.58 | closed |

## Things that went wrong along the way

FINDINGS.md has a section on engine bugs. The one that mattered most here: with
20 years of data and no time limit on a challenge attempt, the simulator
reported an apparent 70% pass rate. That number was an artifact. Some attempts
were taking 7.5 years to pass. Adding the 3/6/12/24-month caps is what produced
the table above. The superseded output is kept in `research/runs/` so the
before and after are both visible.

## Scripts

Research scripts, not a packaged library. Each one prints its results to the
console; the files in `research/runs/` are that output. Run them from the
repository root, for example `python scripts/gold_0900_reversal.py`.

| script | family | what it does |
|---|---|---|
| `ftmo_overlay.py` | 1 | Replays FTMO challenge rules over an equity curve: pass, fail or timeout |
| `ftmo_portfolio_sim.py` | 1 | Donchian portfolio on one balance, rolling challenge starts, breadth test |
| `gold_risk_architecture.py` | 1 | Gold-only sweep of risk per trade against a daily circuit breaker |
| `gold_intraday_probe.py` | 2, 3 | Data inventory and session/timezone diagnosis |
| `gold_calibrate.py` | 2, 3 | Calibrates the broker server clock to GMT from the data |
| `gold_intraday_stats.py` | 2, 3 | Intraday momentum replication, autocorrelation, cost ladder |
| `volume_profile_research.py` | 4 | Descriptive study of value-area extensions and reversion to POC |
| `va_migration_test.py` | 4 | Value-area migration backtest |
| `coint_research.py` | 5 | Rolling Engle-Granger cointegration study, M30 and H1 |
| `coint_d1.py` | 5 | The same study on D1 with 250 and 500-day windows |
| `avellaneda_lee.py` | 6 | Avellaneda-Lee residual reversion backtest with null control |
| `gold_0900_reversal.py` | 7 | 09:00 GMT reversal backtest with permutation null |

They need a logged-in MetaTrader 5 terminal to pull bars, so they will not run
as-is on a machine without one. The scripts that read the HistData M1 files
look in `research/data/` unless the `M1_DIR` environment variable points
somewhere else.

## Data

- **Bars:** OANDA MetaTrader 5 demo feed, pulled through the `MetaTrader5`
  Python package. All spread and tick-volume figures are OANDA's, not FTMO's.
  Bar data is not included in this repository.
- **M1 gold:** HistData.com XAUUSD M1, 2026-04-01 → 2026-06-26, 84,796 rows.
  The three monthly CSVs are over 1 MB each and are not included. They can be
  downloaded from histdata.com. The status reports that came with them are in
  `research/data/`.

FINDINGS.md has a section on data problems found in the MT5 feed, such as gold
`tick_volume` being zero before 2025-12-17.

## Requirements

Python with `numpy`, `pandas` and `MetaTrader5` (Windows only, and it needs a
logged-in MT5 terminal). See `requirements.txt`.

Most scripts attach to whatever account the terminal already has open. Four of
them (`ftmo_portfolio_sim.py`, `gold_calibrate.py`, `gold_intraday_probe.py`,
`gold_intraday_stats.py`) read `MT5_TERMINAL`, `MT5_LOGIN`, `MT5_PASSWORD` and
`MT5_SERVER` from environment variables, which they load from a local
`.env.mt5` file. That file is git-ignored. No credentials are stored in this
repository.
