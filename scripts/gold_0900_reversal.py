"""
gold_0900_reversal.py - Test the 09:00 GMT gold reversal.

PROVENANCE: the earlier per-hour autocorrelation study on XAUUSD found
08:00 GMT momentum (t=+4.33) and 09:00 GMT reversal (t=-4.35), both surviving a
Bonferroni correction across all 24 hours. That correction is already spent - so
this script tests 09:00 ONLY and does not search other hours. Re-searching would
spend the correction twice.

SPECIFICATION
  signal   : the 08:00 GMT hourly bar closed "strongly directional"
  DEFINITION (single, pre-committed, NOT tuned): |close-open| >= 0.5*(high-low)
             of the 08:00 bar. i.e. the body is at least half the range, so the
             hour closed decisively. Direction = sign(close-open).
  entry    : OPPOSITE that direction, at the 09:00 GMT bar's OPEN
  stop/tgt : fixed multiples of the 08:00 bar's RANGE (high-low)
             variants: 0.5x stop / 1.0x target   and   1.0x stop / 1.0x target
  time exit: (a) end of the 09:00 bar   (b) +4 hours   - tested SEPARATELY
  data     : XAUUSD H1, full available history

PESSIMISTIC INTRABAR: within any bar, if both stop and target lie inside
[low, high], the STOP is assumed to fill. Single instrument, so unlike the pair
work the intrabar path assumption here is standard and not fabricated.

NULL CONTROL: a permutation test. The observed vector of 08:00 signs is shuffled
(preserving its marginal up/down mix) and the identical rules are re-run, 200
times. This isolates whether the DIRECTION carries information, holding the
dates, the ranges, and all subsequent price action fixed. The real result is
reported as a percentile of that null distribution.

Sizing 1% of CURRENT equity to the stop, so R is well defined. Gold half-spread
0.55 bps (measured from symbol_info) charged on entry and exit.

GATE: win rate > 50%  AND  RETURN/DD > 1.5  AND  max DD < 10%

RUN (MT5 terminal logged in):  python scripts/gold_0900_reversal.py
"""
import sys
import numpy as np
import pandas as pd

SYM = "XAUUSD.sim"
BODY_FRAC = 0.50          # the single pre-committed "strongly directional" rule
VARIANTS = [(0.5, 1.0), (1.0, 1.0)]      # (stop_mult, target_mult) x range08
HOLD_BARS = [1, 4]        # end of the 09:00 bar; +4 hours
RISK = 0.01
START_BAL = 10_000.0
HALF_SPREAD_BPS = 0.55
N_PERM = 200

def connect():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        sys.exit("MT5 attach failed: %s (terminal logged in?)" % (mt5.last_error(),))
    return mt5

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

def load(mt5):
    mt5.symbol_select(SYM, True)
    r = None
    for n in (50_000, 40_000, 20_000):
        r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_H1, 0, n)
        if r is not None and len(r): break
    if r is None: sys.exit("no H1 data")
    d = pd.DataFrame(r); d.index = pd.to_datetime(d["time"], unit="s")
    d = d[["open", "high", "low", "close"]].astype(float)
    off = np.where(eu_dst_mask(d.index), 3, 2)          # EEST / EET -> GMT
    d["gmt"] = d.index - pd.to_timedelta(off, unit="h")
    return d

def build_signals(d):
    """Rows where the GMT-08:00 bar is strongly directional and the very next
    bar is GMT 09:00. Returns index positions and signal metadata."""
    g = pd.DatetimeIndex(d["gmt"])
    hour = g.hour
    o, h, l, c = d["open"].values, d["high"].values, d["low"].values, d["close"].values
    sigs = []
    for i in np.where(hour == 8)[0]:
        if i+1 >= len(d): continue
        if hour[i+1] != 9: continue                     # require the adjacent 09:00 bar
        rng = h[i]-l[i]
        if rng <= 0: continue
        body = c[i]-o[i]
        if abs(body) < BODY_FRAC*rng: continue          # not strongly directional
        sigs.append({"i08": i, "i09": i+1, "rng": rng,
                     "sign08": 1 if body > 0 else -1,
                     "gmt": g[i]})
    return pd.DataFrame(sigs)

def simulate(d, sigs, stop_mult, tgt_mult, hold, signs=None):
    """One trade per signal day, entered at the 09:00 open, opposite to sign08.
    Stop checked before target within every bar (pessimistic)."""
    o, h, l, c = d["open"].values, d["high"].values, d["low"].values, d["close"].values
    n = len(d)
    eq = START_BAL
    curve = np.full(n, np.nan)
    trades = []
    last_fill = 0
    sv = sigs["sign08"].values if signs is None else signs
    for k in range(len(sigs)):
        i09 = int(sigs["i09"].iloc[k]); rng = float(sigs["rng"].iloc[k])
        direction = -int(sv[k])                          # trade OPPOSITE the 08:00 bar
        ent_raw = o[i09]
        sp = ent_raw*HALF_SPREAD_BPS/1e4
        entry = ent_raw + sp if direction > 0 else ent_raw - sp
        stop_d = stop_mult*rng; tgt_d = tgt_mult*rng
        if stop_d <= 0: continue
        stop = entry - stop_d if direction > 0 else entry + stop_d
        tgt = entry + tgt_d if direction > 0 else entry - tgt_d
        risk_cash = eq*RISK
        qty = risk_cash/stop_d
        end = min(i09+hold-1, n-1)
        why, fill = None, None
        for j in range(i09, end+1):
            if direction > 0:
                if l[j] <= stop: why, fill = "stop", stop; break
                if h[j] >= tgt: why, fill = "target", tgt; break
            else:
                if h[j] >= stop: why, fill = "stop", stop; break
                if l[j] <= tgt: why, fill = "target", tgt; break
        if why is None:
            why, fill, j = "time", c[end], end
        spx = fill*HALF_SPREAD_BPS/1e4
        exitp = fill - spx if direction > 0 else fill + spx
        pnl = qty*(exitp-entry) if direction > 0 else qty*(entry-exitp)
        eq += pnl
        trades.append({"t": d.index[i09], "gmt": sigs["gmt"].iloc[k], "why": why,
                       "pnl": pnl, "R": pnl/risk_cash, "dir": direction})
        curve[last_fill:j+1] = eq
        last_fill = j+1
    curve[last_fill:] = eq
    cv = pd.Series(curve, index=d.index).ffill().dropna()
    return cv, pd.DataFrame(trades)

def stats(cv, tr, years):
    if tr.empty: return None
    ret = cv.iloc[-1]/START_BAL - 1
    dd = (cv/cv.cummax()-1).min()
    cagr = (1+ret)**(1/years)-1 if ret > -1 else np.nan
    wr = 100*(tr["pnl"] > 0).mean()
    return {"n": len(tr), "wr": wr, "ret": ret, "cagr": cagr, "dd": dd,
            "rdd": abs(cagr/dd) if dd else np.nan,
            "tpy": len(tr)/years, "exp_R": tr["R"].mean(),
            "why": tr["why"].value_counts().to_dict(),
            "gate": bool(wr > 50 and dd and abs(cagr/dd) > 1.5 and abs(dd) < 0.10)}

def main():
    mt5 = connect()
    d = load(mt5)
    si = mt5.symbol_info(SYM)
    mt5.shutdown()
    years = (d.index[-1]-d.index[0]).days/365.25
    print("XAUUSD H1: %d bars  %s -> %s  (%.2f years)"
          % (len(d), d["gmt"].iloc[0], d["gmt"].iloc[-1], years))
    if si and si.ask > 0:
        print("live half-spread now: %.3f bps (using %.2f bps)"
              % (1e4*(si.ask-si.bid)/2/(0.5*(si.bid+si.ask)), HALF_SPREAD_BPS))
    sigs = build_signals(d)
    n08 = int((pd.DatetimeIndex(d["gmt"]).hour == 8).sum())
    print("GMT-08:00 bars: %d   strongly directional (body >= %.0f%% of range): %d (%.1f%%)"
          % (n08, 100*BODY_FRAC, len(sigs), 100*len(sigs)/max(n08, 1)))
    print("  up-bars %d / down-bars %d"
          % (int((sigs['sign08'] == 1).sum()), int((sigs['sign08'] == -1).sum())))
    print("  rule is the SINGLE pre-committed definition - not tuned\n")

    rows = []
    for (sm, tm) in VARIANTS:
        for hold in HOLD_BARS:
            lbl = "stop %.1fx / tgt %.1fx, hold %dh" % (sm, tm, hold)
            cv, tr = simulate(d, sigs, sm, tm, hold)
            s = stats(cv, tr, years)
            if s is None: continue
            # split-half stability
            mid = sigs["gmt"].iloc[len(sigs)//2]
            a = tr[tr["gmt"] <= mid]; b = tr[tr["gmt"] > mid]
            s["h1"] = (100*(a["pnl"] > 0).mean(), a["R"].mean(), len(a)) if len(a) > 10 else None
            s["h2"] = (100*(b["pnl"] > 0).mean(), b["R"].mean(), len(b)) if len(b) > 10 else None
            # permutation null on the SIGN of the 08:00 bar
            rng = np.random.default_rng(20260730)
            base = sigs["sign08"].values
            nw, ne = [], []
            for _ in range(N_PERM):
                perm = rng.permutation(base)
                _, tp = simulate(d, sigs, sm, tm, hold, signs=perm)
                if tp.empty: continue
                nw.append(100*(tp["pnl"] > 0).mean()); ne.append(tp["R"].mean())
            nw, ne = np.array(nw), np.array(ne)
            s["null_wr"] = (nw.mean(), nw.std(), np.mean(nw >= s["wr"]))
            s["null_e"] = (ne.mean(), ne.std(), np.mean(ne >= s["exp_R"]))
            s["label"] = lbl
            rows.append(s)
            print("  ran %s" % lbl)

    print("\n" + "="*112)
    print("09:00 GMT GOLD REVERSAL - RESULTS")
    print("="*112)
    print("%-28s %6s %7s %8s %8s %8s %8s %9s %6s"
          % ("variant", "trades", "win%", "exp R", "ret%", "maxDD%", "RET/DD", "trd/yr", "GATE"))
    for s in rows:
        print("%-28s %6d %6.1f%% %8.3f %7.1f%% %7.1f%% %8s %9.1f %6s"
              % (s["label"], s["n"], s["wr"], s["exp_R"], 100*s["ret"], 100*s["dd"],
                 "%.2f" % s["rdd"] if np.isfinite(s["rdd"]) else "-", s["tpy"],
                 "PASS" if s["gate"] else "fail"))

    print("\n--- NULL CONTROL: 08:00 signs permuted, %d runs (does DIRECTION inform?) ---" % N_PERM)
    print("%-28s %16s %16s %12s %12s"
          % ("variant", "real win% / null", "real expR / null", "p(win)", "p(expR)"))
    for s in rows:
        nw, ne = s["null_wr"], s["null_e"]
        print("%-28s %7.1f%% /%6.1f%% %8.3f /%7.3f %11.3f %11.3f"
              % (s["label"], s["wr"], nw[0], s["exp_R"], ne[0], nw[2], ne[2]))
    print("  p = fraction of permutations at least as good as the real result.")
    print("  p > 0.05 means the real result is indistinguishable from random direction.")

    print("\n--- SPLIT-HALF STABILITY ---")
    print("%-28s %24s %24s" % ("variant", "first half win%/expR/n", "second half win%/expR/n"))
    for s in rows:
        f = "%.1f%% / %+.3f / %d" % s["h1"] if s["h1"] else "-"
        g = "%.1f%% / %+.3f / %d" % s["h2"] if s["h2"] else "-"
        print("%-28s %24s %24s" % (s["label"], f, g))

    print("\n--- exit mix ---")
    for s in rows: print("  %-28s %s" % (s["label"], s["why"]))

    print("\n" + "="*112)
    print("GATE: win rate > 50%  AND  RETURN/DD > 1.5  AND  max DD < 10%")
    print("="*112)
    p = [s for s in rows if s["gate"]]
    print("variants passing all three: %d of %d" % (len(p), len(rows)))
    for s in p: print("   PASS: %s" % s["label"])
    print("\n4 variants tested (2 stop/target x 2 holds) - the specified grid, not a")
    print("search. 09:00 only; no other hour examined. Threshold not tuned.")

if __name__ == "__main__":
    main()
