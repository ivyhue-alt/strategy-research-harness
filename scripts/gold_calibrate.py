"""
gold_calibrate.py - STEP 0b: calibrate the server->GMT offset from data, not assumption.

The stale weekend tick makes the clock-difference method useless, and the broker's
M30 series has no visible halt to anchor on. So we calibrate on VOLATILITY SHAPE:
  - HistData M1 has a hard 17:00-17:59 hole = the COMEX 17:00 ET halt, which places
    that file's clock in ET. Apr-Jun 2026 is EDT, so M1 -> GMT = +4h (verified here).
  - We then build the mean |M30 return| profile from BOTH sources and find the
    integer half-hour shift of the broker series that best matches the M1 profile.
That shift IS the server->GMT offset, derived from the instrument's own behaviour.
"""
import os, sys, glob
import numpy as np
import pandas as pd

SYM = "XAUUSD.sim"
# HistData XAUUSD M1 CSVs (not in the repo). Override with the M1_DIR env var.
M1_DIR = os.getenv("M1_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "research", "data"))

def load_env(p=".env.mt5"):
    for ln in open(p):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def load_m1():
    d = []
    for f in sorted(glob.glob(os.path.join(M1_DIR, "*.csv"))):
        x = pd.read_csv(f, header=None, names=["d","t","open","high","low","close","vol"])
        x["dt"] = pd.to_datetime(x["d"]+" "+x["t"], format="%Y.%m.%d %H:%M")
        d.append(x)
    m = pd.concat(d, ignore_index=True).sort_values("dt").set_index("dt")
    return m[["open","high","low","close"]]

def halfhour_absret_profile(close, label):
    """Mean |log return| of 30-min closes, keyed by 'HH:MM' of the bar end."""
    c = close.resample("30min").last().dropna()
    r = np.log(c/c.shift(1)).dropna()*1e4          # bps
    prof = r.abs().groupby(r.index.strftime("%H:%M")).mean()
    n = r.abs().groupby(r.index.strftime("%H:%M")).size()
    return prof, n

def main():
    load_env()
    m1 = load_m1()
    print("M1 rows: %d  %s -> %s" % (len(m1), m1.index[0], m1.index[-1]))

    # ---- confirm the halt hour in the M1 file clock ----
    present = sorted(set(m1.index.hour))
    missing = [h for h in range(24) if h not in present]
    print("M1 file-clock hours entirely ABSENT: %s" % missing)
    print("   -> COMEX halt is 17:00 ET; halt seen at file-hour %s" % missing)
    if missing == [17]:
        M1_TO_GMT = 4     # 17:00 EDT = 21:00 GMT
        print("   -> file clock = ET with DST (EDT) => M1 + 4h = GMT")
    else:
        M1_TO_GMT = 5
        print("   -> assuming fixed EST => M1 + 5h = GMT  (CHECK THIS)")

    m1g = m1.copy(); m1g.index = m1g.index + pd.Timedelta(hours=M1_TO_GMT)
    p_m1, n_m1 = halfhour_absret_profile(m1g["close"], "M1->GMT")

    # ---- broker M30 ----
    import MetaTrader5 as mt5
    if not mt5.initialize(os.environ["MT5_TERMINAL"], login=int(os.environ["MT5_LOGIN"]),
                          password=os.environ["MT5_PASSWORD"], server=os.environ["MT5_SERVER"]):
        sys.exit("MT5 init failed: %s" % (mt5.last_error(),))
    mt5.symbol_select(SYM, True)
    r = None
    for n in (50_000, 40_000, 20_000):      # terminal caps out around 50k bars
        r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M30, 0, n)
        if r is not None and len(r): break
    if r is None or not len(r): sys.exit("no M30 data: %s" % (mt5.last_error(),))
    mt5.shutdown()
    d = pd.DataFrame(r); d.index = pd.to_datetime(d["time"], unit="s")
    d = d[["open","high","low","close"]]
    print("\nM30 broker rows: %d  %s -> %s" % (len(d), d.index[0], d.index[-1]))

    # restrict broker data to the M1 overlap window for a like-for-like profile
    lo, hi = m1g.index[0], m1g.index[-1]
    prof_srv = {}
    for shift in range(-12, 13):
        ds = d.copy(); ds.index = ds.index + pd.Timedelta(hours=shift)
        seg = ds.loc[(ds.index >= lo) & (ds.index <= hi)]
        if len(seg) < 500: continue
        p, _ = halfhour_absret_profile(seg["close"], "srv%+d" % shift)
        common = p.index.intersection(p_m1.index)
        if len(common) < 40: continue
        prof_srv[shift] = np.corrcoef(p[common], p_m1[common])[0,1]
    print("\n--- correlation of broker |ret| profile vs M1(GMT) profile, by assumed shift ---")
    for k in sorted(prof_srv, key=lambda k: -prof_srv[k])[:6]:
        print("   server %+d h -> GMT : corr %.4f" % (k, prof_srv[k]))
    best = max(prof_srv, key=prof_srv.get)
    print("   BEST SHIFT: server %+d h = GMT" % best)

    # ---- show the two profiles side by side at the best shift ----
    ds = d.copy(); ds.index = ds.index + pd.Timedelta(hours=best)
    seg = ds.loc[(ds.index >= lo) & (ds.index <= hi)]
    p_srv, n_srv = halfhour_absret_profile(seg["close"], "srv")
    print("\n--- mean |30-min return| in bps, by GMT half-hour (overlap window) ---")
    print("  GMT     broker   M1     (n_broker)")
    for k in sorted(set(p_srv.index) | set(p_m1.index)):
        print("  %-6s %7.1f %7.1f   %5d" % (k, p_srv.get(k, np.nan),
                                            p_m1.get(k, np.nan), n_srv.get(k, 0)))

    # ---- where is the quiet zone in GMT on the FULL broker history? ----
    dfull = d.copy(); dfull.index = dfull.index + pd.Timedelta(hours=best)
    pf, nf = halfhour_absret_profile(dfull["close"], "full")
    print("\n--- FULL broker history, quietest 6 GMT half-hours (candidate halt) ---")
    for k, v in pf.nsmallest(6).items(): print("   %s  %6.1f bps  n=%d" % (k, v, nf[k]))
    print("--- loudest 6 GMT half-hours ---")
    for k, v in pf.nlargest(6).items(): print("   %s  %6.1f bps  n=%d" % (k, v, nf[k]))

if __name__ == "__main__":
    main()
