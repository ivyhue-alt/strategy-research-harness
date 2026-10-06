"""
gold_risk_architecture.py - Can XAUUSD alone clear a 60% FTMO 2-Step pass rate?

THE LEVER, AND WHY IT IS THE RIGHT ONE
The challenge is a first-passage problem: reach +10% before -10%, never -5% in a
single day. For a positive-drift random walk, P(hit target before floor) is
governed by 2*mu/sigma^2. Scaling position size by k scales BOTH mu and sigma by
k, so 2*mu/sigma^2 scales as 1/k. Halving risk per trade therefore RAISES pass
probability, and a fixed 5% daily limit becomes progressively unreachable. This
directly attacks the constraint that caused ~40% of failures in every prior test.
The cost is time-to-pass, not probability - which matters only if the challenge
is time-limited (see CAVEAT below).

SIGNAL IS HELD FIXED (the validated Donchian config): entry 20, exit 55,
ADX >= 20, 2xATR stop, H4, XAUUSD only, one position at a time. This is a
RISK-ARCHITECTURE test, not a new strategy - one lever at a time.

WHAT IS SWEPT
  risk per trade : 0.25%, 0.50%, 0.75%, 1.00% of CURRENT equity
  daily breaker  : none | flatten at -2% intraday | flatten at -3% intraday
                   (flatten, not merely block new entries, because the daily
                   limit is EQUITY-based including floating P&L - blocking
                   entries does not stop an open loss from deepening)

RULES AS CONFIRMED BY FTMO SUPPORT (2026-07-29)
  * daily loss and max loss are EQUITY-based INCLUDING floating P&L
  * max loss = 10% of the phase's initial balance, STATIC (2-Step)
  * trading day rolls at midnight PRAGUE (CET/CEST) - DST-aware here
  * phase 2 restarts at the phase-1 starting balance with a +5% target; because
    sizing is proportional the path is scale-invariant, so on a relative equity
    curve this is simply "new base = equity at phase transition"
  * min 4 trading days per phase is enforced (conservative; FTMO has relaxed
    this on some products)

TWO PASS-RATE METRICS, AND THE DIFFERENCE MATTERS
  PASS%       = passed within the data available after the start bar
  PASS|res    = PASS / (PASS + FAIL) - pass rate CONDITIONAL ON RESOLVING
  TIMEOUT here means "data ran out", NOT "failed". FTMO Challenge/Verification
  currently have no time limit, so PASS|res is the better estimate of true pass
  probability. If the product IS time-limited, PASS% is the relevant figure and
  low-risk settings are penalised for being slow. Both are reported.

Pessimistic intrabar throughout: stop wins ties.

RUN (MT5 terminal logged in):  python scripts/gold_risk_architecture.py
"""
import sys
import numpy as np
import pandas as pd

SYM = "XAUUSD.sim"
DON_ENTRY, DON_EXIT, ADX_MIN = 20, 55, 20.0
ATR_N = ADX_N = 14
ATR_MULT = 2.0
START_BAL = 10_000.0

RISKS = [0.0025, 0.0050, 0.0075, 0.0100]
BREAKERS = [None, 0.02, 0.03]

P1_TARGET, P2_TARGET = 0.10, 0.05
DAILY_LOSS, MAX_LOSS = 0.05, 0.10
MIN_DAYS = 4
START_EVERY = 60            # a new challenge start every 60 H4 bars (~10 days)

def connect():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        sys.exit("MT5 attach failed: %s (terminal logged in?)" % (mt5.last_error(),))
    return mt5

def wilder(s, n): return s.ewm(alpha=1.0/n, adjust=False).mean()

def prep(d):
    hi, lo, cl = d["high"], d["low"], d["close"]
    prev = cl.shift(1)
    tr = pd.concat([hi-lo, (hi-prev).abs(), (lo-prev).abs()], axis=1).max(axis=1)
    d = d.copy()
    d["atr"] = wilder(tr, ATR_N)
    up, dn = hi.diff(), -lo.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = wilder(tr, ADX_N)
    pdi = 100*wilder(pd.Series(pdm, index=d.index), ADX_N)/a
    mdi = 100*wilder(pd.Series(mdm, index=d.index), ADX_N)/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    d["adx"] = wilder(dx.fillna(0), ADX_N)
    d["don_hi"] = hi.rolling(DON_ENTRY).max().shift(1)
    d["don_lo"] = lo.rolling(DON_ENTRY).min().shift(1)
    d["exit_hi"] = hi.rolling(DON_EXIT).max().shift(1)
    d["exit_lo"] = lo.rolling(DON_EXIT).min().shift(1)
    return d.dropna()

def eu_dst_mask(idx):
    idx = pd.DatetimeIndex(idx)
    out = np.zeros(len(idx), dtype=bool)
    for y in sorted(set(idx.year)):
        mar = pd.Timestamp(year=y, month=3, day=31)
        while mar.dayofweek != 6: mar -= pd.Timedelta(days=1)
        oc = pd.Timestamp(year=y, month=10, day=31)
        while oc.dayofweek != 6: oc -= pd.Timedelta(days=1)
        out |= (idx >= mar+pd.Timedelta(hours=1)) & (idx < oc+pd.Timedelta(hours=1))
    return out

def prague_dates(idx):
    """Broker server clock is EET/EEST; Prague is CET/CEST -> constant -1h."""
    return pd.DatetimeIndex(pd.DatetimeIndex(idx) - pd.Timedelta(hours=1)).date

# ------------------------------------------------------------------ simulation
def simulate(d, risk, breaker, spread_half_pct=0.0055):
    """One position at a time, risk% of CURRENT equity, pessimistic intrabar.
    breaker: None, or a fraction - flatten and stand down for the rest of the
    Prague day once equity is that far below the day's opening equity."""
    hi = d["high"].values; lo = d["low"].values; cl = d["close"].values
    atr = d["atr"].values; adx = d["adx"].values
    dhi = d["don_hi"].values; dlo = d["don_lo"].values
    ehi = d["exit_hi"].values; elo = d["exit_lo"].values
    pdate = prague_dates(d.index)
    n = len(d)
    eq = START_BAL
    curve = np.empty(n); active = np.zeros(n, dtype=bool)
    trades = []
    pos = None
    cur_day = None; day_open_eq = eq; halted = False

    for i in range(n):
        sp = cl[i]*spread_half_pct/100.0
        # ---- day rollover (Prague): day_open_eq is equity carried INTO the day,
        # i.e. mark-to-market at the previous bar's close (floating included) ----
        if pdate[i] != cur_day:
            cur_day = pdate[i]
            if pos is None or i == 0:
                day_open_eq = eq
            else:
                day_open_eq = eq + pos["qty"]*(cl[i-1] - pos["entry"])
            halted = False
        # ---- manage open position: stop first (pessimistic) ----
        if pos is not None:
            p = pos; long = p["qty"] > 0
            hit = (lo[i] <= p["stop"]) if long else (hi[i] >= p["stop"])
            done = False
            if hit:
                fill = p["stop"]; done = True
            elif (long and cl[i] <= elo[i]) or ((not long) and cl[i] >= ehi[i]):
                fill = cl[i]; done = True
            if done:
                px = fill - sp if long else fill + sp
                pnl = p["qty"]*(px - p["entry"])
                eq += pnl
                trades.append({"i": i, "t": d.index[i], "pnl": pnl,
                               "R": pnl/p["risk_cash"] if p["risk_cash"] else np.nan})
                pos = None
        # ---- mark to market ----
        mtm = eq if pos is None else eq + pos["qty"]*(cl[i]-pos["entry"])
        # ---- daily circuit breaker: flatten + stand down ----
        if breaker is not None and not halted and mtm <= day_open_eq*(1-breaker):
            if pos is not None:
                long = pos["qty"] > 0
                px = cl[i] - sp if long else cl[i] + sp
                pnl = pos["qty"]*(px - pos["entry"])
                eq += pnl
                trades.append({"i": i, "t": d.index[i], "pnl": pnl,
                               "R": pnl/pos["risk_cash"] if pos["risk_cash"] else np.nan})
                pos = None
                mtm = eq
            halted = True
        # ---- entries ----
        if pos is None and not halted and adx[i] >= ADX_MIN and atr[i] > 0:
            dist = ATR_MULT*atr[i]
            if cl[i] > dhi[i]:
                e = cl[i] + sp
                pos = {"qty": (eq*risk)/dist, "entry": e, "stop": e-dist,
                       "risk_cash": eq*risk}
            elif cl[i] < dlo[i]:
                e = cl[i] - sp
                pos = {"qty": -(eq*risk)/dist, "entry": e, "stop": e+dist,
                       "risk_cash": eq*risk}
            if pos is not None:
                # entry bar: floating loss equals the half-spread crossed
                mtm = eq + pos["qty"]*(cl[i] - pos["entry"])
        curve[i] = mtm
        active[i] = pos is not None
    return pd.Series(curve, index=d.index), pd.DataFrame(trades), active, pdate

# ------------------------------------------------------------------ challenge
def challenge_from(curve, pdate, active, i0, max_bars=None):
    """FTMO 2-Step walked forward from bar i0. Equity-based, incl. floating.
    max_bars caps how long the attempt may run - WITHOUT a cap, a window
    starting early gets up to 20 years of runway to reach +15.5%, which
    massively inflates the apparent pass rate. Returns (outcome, bars_used)."""
    v0 = curve[i0]
    phase, target, base = 1, P1_TARGET, v0
    day = pdate[i0]; day_open = v0
    days_traded = set()
    n = len(curve) if max_bars is None else min(len(curve), i0+max_bars)
    for i in range(i0, n):
        v = curve[i]
        if pdate[i] != day:
            day = pdate[i]; day_open = v
        if active[i]: days_traded.add(pdate[i])
        if v <= base*(1-MAX_LOSS): return "FAIL_MAXLOSS", i-i0
        if v <= day_open - base*DAILY_LOSS: return "FAIL_DAILY", i-i0
        if v >= base*(1+target) and len(days_traded) >= MIN_DAYS:
            if phase == 2: return "PASS", i-i0
            phase, target, base = 2, P2_TARGET, v
            day_open = v; days_traded = set()
    return "TIMEOUT", n-i0

def evaluate(curve, trades, active, pdate, label, bars_per_year, horizons_m=(3, 6, 12, 24)):
    cv = curve.values
    # ---- pass rate within REALISTIC horizons: this is the number that matters ----
    horizon_pass = {}
    for m in horizons_m:
        cap = int(bars_per_year*m/12.0)
        rr = [challenge_from(cv, pdate, active, i, max_bars=cap)
              for i in range(0, len(cv)-200, START_EVERY)]
        o = pd.Series([x[0] for x in rr]).value_counts()
        horizon_pass[m] = 100*int(o.get("PASS", 0))/len(rr)
    res = [challenge_from(cv, pdate, active, i) for i in range(0, len(cv)-200, START_EVERY)]
    oc = pd.Series([r[0] for r in res]).value_counts()
    npass = int(oc.get("PASS", 0)); nfd = int(oc.get("FAIL_DAILY", 0))
    nfm = int(oc.get("FAIL_MAXLOSS", 0)); nto = int(oc.get("TIMEOUT", 0))
    tot = len(res); resolved = npass + nfd + nfm
    ret = cv[-1]/START_BAL - 1
    dd = (curve/curve.cummax()-1).min()
    yrs = (curve.index[-1]-curve.index[0]).days/365.25
    cagr = (1+ret)**(1/yrs)-1 if ret > -1 else np.nan
    wr = 100*(trades["pnl"] > 0).mean() if len(trades) else np.nan
    bars = [r[1] for r in res if r[0] == "PASS"]
    return {"label": label, "n_starts": tot,
            "pass_pct": 100*npass/tot, "pass_res": 100*npass/resolved if resolved else np.nan,
            "fd": 100*nfd/tot, "fm": 100*nfm/tot, "to": 100*nto/tot,
            "ret": ret, "cagr": cagr, "dd": dd,
            "rdd": abs(cagr/dd) if dd else np.nan,
            "trades": len(trades), "wr": wr,
            "med_bars": np.median(bars) if bars else np.nan,
            "med_years": (np.median(bars)/bars_per_year) if bars else np.nan,
            "hz": horizon_pass}

def main():
    mt5 = connect()
    mt5.symbol_select(SYM, True)
    r = None
    for n in (50_000, 32_000, 20_000):
        r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_H4, 0, n)
        if r is not None and len(r): break
    mt5.shutdown()
    if r is None: sys.exit("no H4 data")
    raw = pd.DataFrame(r); raw.index = pd.to_datetime(raw["time"], unit="s")
    d = prep(raw[["open", "high", "low", "close"]])
    print("XAUUSD H4: %d bars  %s -> %s  (%.2f years)"
          % (len(d), d.index[0].date(), d.index[-1].date(),
             (d.index[-1]-d.index[0]).days/365.25))
    print("signal held FIXED at the validated config: Donchian 20/55, ADX>=20, 2xATR")
    print("sweeping RISK PER TRADE and the DAILY CIRCUIT BREAKER only\n")

    yrs = (d.index[-1]-d.index[0]).days/365.25
    bpy = len(d)/yrs
    print("bars/year = %.0f  (so a 12-month attempt cap = %d H4 bars)\n" % (bpy, int(bpy)))

    rows = []
    for risk in RISKS:
        for br in BREAKERS:
            lbl = "risk %.2f%% / breaker %s" % (100*risk, "none" if br is None else "-%.0f%%" % (100*br))
            curve, tr, act, pdate = simulate(d, risk, br)
            rows.append(evaluate(curve, tr, act, pdate, lbl, bpy))
            print("  done: %s" % lbl)

    print("\n" + "="*118)
    print("PASS RATE WITHIN A REALISTIC TIME CAP  <-- THE NUMBERS THAT MATTER")
    print("XAUUSD H4 alone, %.1f years, %d challenge starts per config" % (yrs, rows[0]["n_starts"]))
    print("A challenge nobody would actually sit through is not a pass.")
    print("="*118)
    print("%-30s %9s %9s %9s %9s %11s %10s"
          % ("config", "3 months", "6 months", "12 months", "24 months",
             "UNCAPPED", "med yrs"))
    for r_ in rows:
        print("%-30s %8.1f%% %8.1f%% %8.1f%% %8.1f%% %10.1f%% %9s"
              % (r_["label"], r_["hz"][3], r_["hz"][6], r_["hz"][12], r_["hz"][24],
                 r_["pass_pct"],
                 "%.1f" % r_["med_years"] if np.isfinite(r_["med_years"]) else "-"))

    print("\n" + "="*118)
    print("SUPPORTING DETAIL (uncapped run: failure modes and strategy quality)")
    print("="*118)
    print("%-30s %8s %8s %8s %8s %7s %7s"
          % ("config", "FAILday", "FAILmax", "TIMEOUT", "maxDD%", "RET/DD", "win%"))
    for r_ in rows:
        print("%-30s %7.1f%% %7.1f%% %7.1f%% %7.1f%% %7.2f %6.1f%%"
              % (r_["label"], r_["fd"], r_["fm"], r_["to"], 100*r_["dd"],
                 r_["rdd"], r_["wr"]))

    print("\n" + "="*118)
    print("TARGET: 60%+ pass rate")
    print("="*118)
    for m in (3, 6, 12, 24):
        hit = [r_ for r_ in rows if r_["hz"][m] >= 60]
        best = max(rows, key=lambda x: x["hz"][m])
        print("  within %2d months: %d of %d configs reach 60%%   (best: %s at %.1f%%)"
              % (m, len(hit), len(rows), best["label"], best["hz"][m]))
    print("\nWHY THE UNCAPPED COLUMN IS MISLEADING: with 20 years of data, an attempt")
    print("starting in 2006 may run for years before reaching +15.5%% compounded")
    print("(10%% then 5%%). That inflates 'PASS%%'. The capped columns are the honest")
    print("estimate of what a challenge attempt would actually deliver.")
    print("Descriptive backtest on one instrument; not a guarantee of live outcome.")

if __name__ == "__main__":
    main()
