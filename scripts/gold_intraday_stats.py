"""
gold_intraday_stats.py - Intraday descriptive statistics for XAUUSD. NO strategy,
no trading logic, no backtest. Replicates the Gao/Han/Li/Zhou (2018) intraday
momentum test and reports the microstructure facts needed to judge whether an
intraday family is viable at all.

CALIBRATED FACTS (established in gold_calibrate.py, not assumed):
  - broker server clock = EET/EEST  =>  GMT = server - 3 (summer) / - 2 (winter).
    Verified by matching the broker's mean |30m return| profile against HistData
    M1 placed in GMT independently: corr 0.9998 at -3h, 0.38 at the next shift.
  - gold's day = 46 half-hour buckets, server 01:00..23:30. Server 00:00-00:59
    has ZERO bars = the COMEX 17:00 ET halt (21:00-22:00 GMT in summer).
  - the day is defined in SERVER time because that clock is session-stable; only
    the vol/spread tables use true DST-aware GMT, as requested.

DESIGN NOTE the reader must weigh: in the original paper the last half-hour is
the US equity close - the most liquid bucket of the day. Gold's last pre-halt
bucket (20:30 GMT) is its QUIETEST (5.6 bps vs 20.0 bps at the US open). So the
paper's structure does not map cleanly onto gold's day, and we report BOTH:
  (A) gold's own full day, 46 buckets, target = final pre-halt bucket
  (B) a US-session analogue, 13 buckets 9:30-16:00 ET, target = the equity close
      (this is the true structural analogue of the paper)

RUN (MT5 terminal open):  python scripts/gold_intraday_stats.py
"""
import os, sys, glob
import numpy as np
import pandas as pd

SYM = "XAUUSD.sim"
# HistData XAUUSD M1 CSVs (not in the repo). Override with the M1_DIR env var.
M1_DIR = os.getenv("M1_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "research", "data"))

HALF_SPREAD_PCT = 0.0053      # stated account half-spread, in percent
SNAPSHOT_HALF_PCT = 0.01345   # observed at Friday close (weekend-widened upper bound)

# ---------------------------------------------------------------- infrastructure
def load_env(p=".env.mt5"):
    for ln in open(p):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def eu_dst_mask(idx):
    """EU summer time: last Sun Mar 01:00 UTC -> last Sun Oct 01:00 UTC."""
    idx = pd.DatetimeIndex(idx)
    out = np.zeros(len(idx), dtype=bool)
    for y in sorted(set(idx.year)):
        mar = pd.Timestamp(year=y, month=3, day=31)
        while mar.dayofweek != 6: mar -= pd.Timedelta(days=1)
        oct_ = pd.Timestamp(year=y, month=10, day=31)
        while oct_.dayofweek != 6: oct_ -= pd.Timedelta(days=1)
        out |= (idx >= mar + pd.Timedelta(hours=1)) & (idx < oct_ + pd.Timedelta(hours=1))
    return out

def us_dst_mask(idx):
    """US summer time: 2nd Sun Mar 02:00 -> 1st Sun Nov 02:00 (local, approx)."""
    idx = pd.DatetimeIndex(idx)
    out = np.zeros(len(idx), dtype=bool)
    for y in sorted(set(idx.year)):
        mar = pd.Timestamp(year=y, month=3, day=1)
        while mar.dayofweek != 6: mar += pd.Timedelta(days=1)
        mar += pd.Timedelta(days=7, hours=2)
        nov = pd.Timestamp(year=y, month=11, day=1)
        while nov.dayofweek != 6: nov += pd.Timedelta(days=1)
        nov += pd.Timedelta(hours=2)
        out |= (idx >= mar) & (idx < nov)
    return out

def ols_nw(y, x, lags=None):
    """OLS y = a + b*x with Newey-West (Bartlett) HAC t-stat. Returns b,t,R2,n."""
    y = np.asarray(y, float); x = np.asarray(x, float)
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    n = len(y)
    if n < 30: return np.nan, np.nan, np.nan, n
    X = np.column_stack([np.ones(n), x])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    if lags is None: lags = int(np.floor(4*(n/100)**(2/9)))
    XtXi = np.linalg.inv(X.T @ X)
    Xe = X * e[:, None]
    S = Xe.T @ Xe
    for L in range(1, lags+1):
        w = 1.0 - L/(lags+1.0)
        G = Xe[L:].T @ Xe[:-L]
        S += w*(G + G.T)
    V = XtXi @ S @ XtXi
    se = np.sqrt(np.diag(V))
    sst = ((y - y.mean())**2).sum()
    r2 = 1.0 - (e @ e)/sst if sst > 0 else np.nan
    return b[1], b[1]/se[1] if se[1] > 0 else np.nan, r2, n

def corr_t(a, b):
    """Pearson r with t-stat and n, on the finite overlap."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    n = len(a)
    if n < 30: return np.nan, np.nan, n
    r = np.corrcoef(a, b)[0, 1]
    t = r*np.sqrt(n-2)/np.sqrt(max(1-r*r, 1e-12))
    return r, t, n

# ---------------------------------------------------------------- data
def get_m30():
    load_env()
    import MetaTrader5 as mt5
    if not mt5.initialize(os.environ["MT5_TERMINAL"], login=int(os.environ["MT5_LOGIN"]),
                          password=os.environ["MT5_PASSWORD"], server=os.environ["MT5_SERVER"]):
        sys.exit("MT5 init failed: %s" % (mt5.last_error(),))
    mt5.symbol_select(SYM, True)
    r = None
    for n in (50_000, 40_000, 20_000):
        r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M30, 0, n)
        if r is not None and len(r): break
    mt5.shutdown()
    if r is None or not len(r): sys.exit("no M30 data")
    d = pd.DataFrame(r)
    d.index = pd.to_datetime(d["time"], unit="s")     # SERVER clock
    d = d[["open", "high", "low", "close"]].copy()
    off = np.where(eu_dst_mask(d.index), 3, 2)        # EEST / EET
    d["gmt"] = d.index - pd.to_timedelta(off, unit="h")
    et_off = np.where(us_dst_mask(d["gmt"]), 4, 5)    # EDT / EST from GMT
    d["et"] = d["gmt"] - pd.to_timedelta(et_off, unit="h")
    return d

def get_m1():
    fs = sorted(glob.glob(os.path.join(M1_DIR, "*.csv")))
    if not fs: return None
    out = []
    for f in fs:
        x = pd.read_csv(f, header=None, names=["d","t","open","high","low","close","vol"])
        x["dt"] = pd.to_datetime(x["d"]+" "+x["t"], format="%Y.%m.%d %H:%M")
        out.append(x)
    m = pd.concat(out, ignore_index=True).sort_values("dt").set_index("dt")
    m = m[["open","high","low","close"]]
    m.index = m.index + pd.Timedelta(hours=4)   # file clock = EDT (halt at 17:00) -> GMT
    return m

# ---------------------------------------------------------------- bucket builder
def build_buckets(d, clock, first, last):
    """Wide frame: rows = trading day, cols = bucket index, values = return in bps.
    Bucket 1 return uses that bar's OPEN (as in the paper); later buckets are
    close-to-close. `first`/`last` are 'HH:MM' bounds on the chosen clock."""
    t = d[clock]
    hhmm = t.dt.strftime("%H:%M")
    inwin = (hhmm >= first) & (hhmm <= last)
    x = d.loc[inwin].copy()
    x["day"] = t[inwin].dt.normalize()
    x["slot"] = hhmm[inwin]
    slots = sorted(x["slot"].unique())
    piv_c = x.pivot_table(index="day", columns="slot", values="close", aggfunc="last")
    piv_o = x.pivot_table(index="day", columns="slot", values="open", aggfunc="first")
    piv_c = piv_c.reindex(columns=slots); piv_o = piv_o.reindex(columns=slots)
    R = pd.DataFrame(index=piv_c.index)
    R[1] = np.log(piv_c[slots[0]]/piv_o[slots[0]])*1e4
    for i in range(1, len(slots)):
        R[i+1] = np.log(piv_c[slots[i]]/piv_c[slots[i-1]])*1e4
    R = R.dropna(how="any")
    return R, slots

def regression_table(R, title, slots, clock_label, note=""):
    """Univariate: r_last = a + b*r_j, for every earlier bucket j."""
    K = R.shape[1]
    y = R[K].values
    print("\n" + "="*94)
    print(title)
    if note: print(note)
    print("target = bucket %d (%s %s)   N days = %d   %s"
          % (K, clock_label, slots[K-1], len(R), "univariate OLS, Newey-West t"))
    print("="*94)
    print("%-6s %-8s %9s %8s %9s %7s   %s" %
          ("bkt", clock_label, "beta", "NW t", "R2 %", "n", "flag"))
    rows = []
    for j in range(1, K):
        b, t, r2, n = ols_nw(y, R[j].values)
        flag = ""
        if abs(t) > 2.58: flag = "**"
        elif abs(t) > 1.96: flag = "*"
        rows.append((j, slots[j-1], b, t, r2, n, flag))
        print("%-6d %-8s %9.4f %8.2f %9.3f %7d   %s" % (j, slots[j-1], b, t, r2*100, n, flag))
    sig = [r for r in rows if abs(r[3]) > 1.96]
    print("-"*94)
    print("predictors with |t|>1.96 : %d of %d   (expected by chance at 5%%: %.1f)"
          % (len(sig), K-1, 0.05*(K-1)))
    bonf = 1.96 if K <= 2 else abs(np.sqrt(2)*_erfinv(1-0.05/(K-1)))
    print("Bonferroni |t| threshold for %d tests: %.2f   surviving: %d"
          % (K-1, bonf, sum(1 for r in rows if abs(r[3]) > bonf)))
    return rows

def _erfinv(y):
    """Inverse error function (Winitzki approx), for the Bonferroni z threshold."""
    a = 0.147
    ln = np.log(1-y*y)
    t1 = 2/(np.pi*a) + ln/2
    return np.sign(y)*np.sqrt(np.sqrt(t1*t1 - ln/a) - t1)

def split_table(R, groups, gname, title, slots, keep=None):
    """beta / t for each predictor within each subgroup (compact)."""
    K = R.shape[1]
    y_all = R[K]
    cols = list(groups.keys())
    preds = keep if keep else list(range(1, K))
    print("\n" + "="*94)
    print(title)
    print("="*94)
    hdr = "%-6s %-8s" % ("bkt", "slot") + "".join("%16s" % c for c in cols)
    print(hdr)
    print("%-6s %-8s" % ("", "") + "".join("%16s" % "beta / t" for c in cols))
    for j in preds:
        line = "%-6d %-8s" % (j, slots[j-1])
        for c in cols:
            m = groups[c]
            b, t, r2, n = ols_nw(y_all[m].values, R[j][m].values)
            line += "%16s" % ("%.3f/%.2f" % (b, t) if np.isfinite(b) else "-")
        print(line)
    print("n days per group: " + "  ".join("%s=%d" % (c, groups[c].sum()) for c in cols))

# ---------------------------------------------------------------- main
def main():
    d = get_m30()
    print("M30 broker bars : %d   server %s -> %s"
          % (len(d), d.index[0], d.index[-1]))
    print("                  GMT    %s -> %s" % (d["gmt"].iloc[0], d["gmt"].iloc[-1]))
    print("span            : %.2f years" % ((d.index[-1]-d.index[0]).days/365.25))

    # ---- validate the DST-aware conversion: the 8:30 ET spike must sit at
    # 12:30 GMT in summer and 13:30 GMT in winter if the offsets are right ----
    r30 = np.log(d["close"]/d["close"].shift(1)).abs()*1e4
    sm = eu_dst_mask(d["gmt"])
    for nm, msk in (("EU summer", sm), ("EU winter", ~sm)):
        g = d["gmt"][msk].dt.strftime("%H:%M")
        prof = r30[msk].groupby(g).mean()
        print("  [validate] %s: loudest GMT half-hours = %s"
              % (nm, ", ".join("%s(%.1f)" % (k, v) for k, v in prof.nlargest(3).items())))
    et = d["et"].dt.strftime("%H:%M")
    prof_et = r30.groupby(et).mean()
    print("  [validate] ET-anchored loudest = %s"
          % ", ".join("%s(%.1f)" % (k, v) for k, v in prof_et.nlargest(3).items()))

    # ================= (A) gold's own day: 46 buckets, server clock =============
    RA, slotsA = build_buckets(d.assign(srv=d.index), "srv", "01:00", "23:30")
    regression_table(
        RA, "TABLE 1 - GAO/HAN/LI/ZHOU REPLICATION on GOLD'S OWN DAY (46 buckets)",
        slotsA, "srv",
        note="server 01:00..23:30 = GMT 22:00..20:30(summer). Target is gold's\n"
             "QUIETEST bucket (5.6 bps), which is NOT analogous to the paper's\n"
             "US-close target - read Table 2 as the true structural analogue.")

    # ================= (B) US session analogue: 13 buckets, ET clock ============
    RB, slotsB = build_buckets(d, "et", "09:30", "15:30")
    rowsB = regression_table(
        RB, "TABLE 2 - US-SESSION ANALOGUE, 9:30-16:00 ET (13 buckets, as the paper)",
        slotsB, "ET",
        note="target = 15:30-16:00 ET bucket (the US equity close), the direct\n"
             "structural analogue of the paper's r13.")

    # ---- volatility terciles (ex-ante: PRIOR day's realised vol) ----
    for R, slots, lbl in ((RA, slotsA, "gold day"), (RB, slotsB, "US session")):
        K = R.shape[1]
        rv = np.sqrt((R.iloc[:, :K-1]**2).sum(axis=1))
        prv = rv.shift(1)                      # strictly ex-ante
        q1, q2 = prv.quantile([1/3, 2/3])
        groups = {"lo-vol": prv <= q1, "mid-vol": (prv > q1) & (prv <= q2), "hi-vol": prv > q2}
        keep = [1, 2, K-2, K-1] if K > 4 else list(range(1, K))
        split_table(R, groups, "vol", "TABLE 3%s - by PRIOR-DAY volatility tercile (%s); "
                    "predictors shown: first two + last two"
                    % ("a" if lbl == "gold day" else "b", lbl), slots, keep=keep)
        dow = R.index.dayofweek
        gd = {n: dow == i for i, n in enumerate(["Mon","Tue","Wed","Thu","Fri"])}
        split_table(R, gd, "dow", "TABLE 4%s - by DAY OF WEEK (%s)"
                    % ("a" if lbl == "gold day" else "b", lbl), slots, keep=keep)

    # ================= per-hour autocorrelation: momentum vs reversal ===========
    h = d.set_index("gmt")["close"].resample("1h").last().dropna()
    rh = (np.log(h/h.shift(1))*1e4).dropna()
    df = pd.DataFrame({"r": rh, "hour": rh.index.hour, "day": rh.index.normalize()})
    df["rnext"] = df["r"].shift(-1)
    df["hnext"] = df["hour"].shift(-1)
    print("\n" + "="*94)
    print("TABLE 5 - PER-HOUR AUTOCORRELATION (GMT): does hour h predict hour h+1?")
    print("  positive = momentum carries into the next hour; negative = reversal")
    print("="*94)
    print("%-8s %10s %8s %8s %12s %10s" % ("GMT hr", "rho(h,h+1)", "t", "n", "mean|r| bps", "verdict"))
    for hh in range(24):
        m = (df["hour"] == hh) & (df["hnext"] == (hh+1) % 24)
        r, t, n = corr_t(df.loc[m, "r"], df.loc[m, "rnext"])
        if not np.isfinite(r): continue
        mabs = df.loc[df["hour"] == hh, "r"].abs().mean()
        v = "MOMENTUM" if t > 1.96 else ("REVERSAL" if t < -1.96 else "")
        print("%-8s %10.4f %8.2f %8d %12.1f %10s" % ("%02d:00" % hh, r, t, n, mabs, v))

    # ---- global lag-1 autocorrelation at M15 / M30 / H1 ----
    print("\n" + "="*94)
    print("TABLE 6 - GLOBAL LAG-1 RETURN AUTOCORRELATION (the family-selection number)")
    print("  >0 favours momentum/breakout;  <0 favours mean reversion")
    print("="*94)
    print("%-24s %10s %8s %8s %10s" % ("series", "rho1", "t", "n", "source"))
    c30 = d.set_index("gmt")["close"]
    for lbl, s, src in (("M30", c30, "broker 4.2y"),
                        ("H1", c30.resample("1h").last(), "broker 4.2y")):
        r = (np.log(s/s.shift(1))).dropna()
        rho, t, n = corr_t(r[:-1], r[1:])
        print("%-24s %10.4f %8.2f %8d %10s" % (lbl, rho, t, n, src))
    m1 = get_m1()
    if m1 is not None:
        for lbl, rule in (("M1", "1min"), ("M5", "5min"), ("M15", "15min"), ("M30", "30min")):
            s = m1["close"].resample(rule).last().dropna()
            r = (np.log(s/s.shift(1))).dropna()
            rho, t, n = corr_t(r[:-1], r[1:])
            print("%-24s %10.4f %8.2f %8d %10s" % (lbl, rho, t, n, "M1 files 3m"))

    # ================= spread cost vs the size of the move =====================
    rt_bps = 2*HALF_SPREAD_PCT*100          # round-trip cost in bps
    rt_snap = 2*SNAPSHOT_HALF_PCT*100
    print("\n" + "="*94)
    print("TABLE 7 - ROUND-TRIP SPREAD COST AS %% OF THE AVERAGE MOVE, BY GMT HOUR")
    print("  stated half-spread %.4f%% -> round trip %.2f bps" % (HALF_SPREAD_PCT, rt_bps))
    print("  Friday-close snapshot %.4f%% -> round trip %.2f bps (weekend-widened,"
          % (SNAPSHOT_HALF_PCT, rt_snap))
    print("  shown as an upper bound; true intraday spread sits between the two)")
    print("")
    print("  *** LIMITATION - THESE FRACTIONS ARE NOT PRECISE ***")
    print("  The terminal reports '49 symbols, 0 spreads': it stores NO per-bar")
    print("  spread history. So a SINGLE constant spread is applied to all 24")
    print("  hours, while the real spread varies intraday and widens when")
    print("  liquidity thins. Every cost figure below is therefore a LOWER BOUND")
    print("  in thin hours - and the pre-halt window (20:00-21:00 GMT), which is")
    print("  both the quietest bucket AND the literature's target half-hour, is")
    print("  exactly where the understatement is worst. Treat the ranking across")
    print("  hours as indicative and the absolute levels as optimistic.")
    print("  Feed note: terminal journal shows OANDA-Demo; these are OANDA demo")
    print("  spreads, not necessarily the eventual execution venue's.")
    print("="*94)
    g30 = d.set_index("gmt")
    r30g = (np.log(g30["close"]/g30["close"].shift(1))*1e4).dropna()
    a30 = r30g.abs().groupby(r30g.index.hour).mean()
    n30 = r30g.abs().groupby(r30g.index.hour).size()
    m15 = None
    if m1 is not None:
        s15 = m1["close"].resample("15min").last().dropna()
        r15 = (np.log(s15/s15.shift(1))*1e4).dropna()
        m15 = r15.abs().groupby(r15.index.hour).mean()
    print("%-8s %12s %10s %10s %12s %10s" %
          ("GMT hr", "mean|M30|", "cost/M30", "snap/M30", "mean|M15|", "cost/M15"))
    for hh in range(24):
        if hh not in a30.index: continue
        v30 = a30[hh]
        c1 = 100*rt_bps/v30; c2 = 100*rt_snap/v30
        if m15 is not None and hh in m15.index:
            v15 = m15[hh]; c3 = 100*rt_bps/v15
            print("%-8s %12.1f %9.1f%% %9.1f%% %12.1f %9.1f%%"
                  % ("%02d:00" % hh, v30, c1, c2, v15, c3))
        else:
            print("%-8s %12.1f %9.1f%% %9.1f%% %12s %10s"
                  % ("%02d:00" % hh, v30, c1, c2, "-", "-"))
    print("-"*94)
    allm30 = r30g.abs().mean()
    print("ALL HOURS  mean|M30| %.1f bps -> cost %.1f%% (stated) / %.1f%% (snapshot)"
          % (allm30, 100*rt_bps/allm30, 100*rt_snap/allm30))
    # ---- TABLE 8: frequency ladder, like-for-like on ONE window ----
    if m1 is not None:
        print("\n" + "="*94)
        print("TABLE 8 - COST vs FREQUENCY, like-for-like (all rows = same M1 window)")
        gseg = d.set_index("gmt")["close"].loc[m1.index[0]:m1.index[-1]]
        r30w = (np.log(gseg/gseg.shift(1))*1e4).dropna().abs().mean()
        r30a = (np.log(d.set_index("gmt")["close"]/
                       d.set_index("gmt")["close"].shift(1))*1e4).dropna().abs().mean()
        k = r30a/r30w
        print("  Mixing sources would flatter the fast timeframes: the M1 files cover")
        print("  Apr-Jun 2026, a %.2fx more volatile stretch than the 4.2y average, so"
              % (r30w/r30a))
        print("  a naive M15-vs-M30 comparison across sources is invalid. Both the raw")
        print("  and regime-adjusted columns are shown.")
        print("="*94)
        print("%-6s %13s %11s %15s %11s %14s"
              % ("tf", "mean|ret|bps", "cost/move", "typ-regime bps", "cost/move", "rho1 (t)"))
        for lbl, rule in (("M1","1min"),("M5","5min"),("M15","15min"),
                          ("M30","30min"),("H1","1h")):
            s = m1["close"].resample(rule).last().dropna()
            r = (np.log(s/s.shift(1))*1e4).dropna()
            v = r.abs().mean(); vn = v*k
            rho, t, n = corr_t(r[:-1], r[1:])
            print("%-6s %13.1f %10.1f%% %15.1f %10.1f%% %14s"
                  % (lbl, v, 100*rt_bps/v, vn, 100*rt_bps/vn, "%.4f (%.1f)" % (rho, t)))
        print("-"*94)
        print("regime scale factor applied: %.2f  (4.2y mean|M30| %.1f / window %.1f bps)"
              % (k, r30a, r30w))
        print("Caveat: cost/move uses the AVERAGE move as the denominator. A real")
        print("strategy captures only a fraction of the average move, so its effective")
        print("cost burden is proportionally higher than these figures.")
    print("\nNOTE: descriptive statistics only. No strategy, no signal, no backtest.")

if __name__ == "__main__":
    main()
