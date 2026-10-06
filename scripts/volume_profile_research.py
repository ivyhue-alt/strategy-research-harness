"""
volume_profile_research.py - DESCRIPTIVE research on prior-session value-area
extensions and reversion to POC. XAUUSD + EURUSD, M15 + M30.

NO strategy, no entries, no backtest, no P&L. Distributions only.

QUESTIONS
  1. When price trades beyond the prior session's VAH/VAL, how does maximum
     favourable excursion BACK TOWARD POC compare with maximum adverse
     excursion FURTHER OUT, over the next 1-12 hours?
  2. What fraction of those extensions revert to POC at all, and how long?
  3. Does the revert rate condition on hour-of-day, or on whether the extension
     came on rising vs falling tick volume (a "tired auction")?

KNOWN LIMITATIONS, stated up front because they bound every number below:
  * TICK VOLUME IS A PROXY. MT5 gives tick count, not traded contracts. For OTC
    gold/FX there is no consolidated tape, so the value area is an APPROXIMATION
    of where business was done, not an exact measure. Treat VAH/VAL/POC as
    indicative levels, not precise ones.
  * INTRA-BAR VOLUME SHAPE IS ASSUMED. Each bar's tick volume is spread
    uniformly across the price bins its high-low spans. Real distribution
    within a bar is unknown at this resolution.
  * BROKER-SPECIFIC. Bars come from one OANDA demo feed; another venue's tick
    counts (and therefore value areas) would differ.
  * NO SPREAD MODEL. Excursions are raw mid-price distances. The terminal
    stores no per-bar spread history, so any cost overlay would be invented.

RUN (MT5 terminal open, any account):  python scripts/volume_profile_research.py
"""
import os, sys
import numpy as np
import pandas as pd

SYMBOLS = ["XAUUSD.sim", "EURUSD.sim"]
TFS = ["M15", "M30"]
VA_PCT = 0.70            # value area = 70% of session tick volume
BIN_BP = 1.0             # price bin width in basis points of price (0.01%)
HORIZONS_H = [1, 2, 3, 4, 6, 8, 12]
MIN_SESSION_BARS = 20    # prior session must have at least this many bars
TIRED_FAST, TIRED_SLOW = 3, 20   # vol ratio = mean(last 3 bars) / mean(prior 20)
THIN_YEARS = 1.0         # below this, flag the instrument as too thin to conclude

def mt5_connect():
    import MetaTrader5 as mt5
    # attach to whatever account the terminal already has open; explicit-login
    # init fails when the stored credentials have gone stale
    if not mt5.initialize():
        sys.exit("MT5 attach failed: %s" % (mt5.last_error(),))
    return mt5

def get_rates(mt5, sym, tfname):
    tf = {"M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30}[tfname]
    mt5.symbol_select(sym, True)
    r = None
    for n in (200_000, 100_000, 50_000, 20_000, 10_000, 5_000, 2_000, 500):
        r = mt5.copy_rates_from_pos(sym, tf, 0, n)
        if r is not None and len(r): break
    if r is None or not len(r): return None
    d = pd.DataFrame(r)
    d.index = pd.to_datetime(d["time"], unit="s")     # SERVER clock
    d = d[["open", "high", "low", "close", "tick_volume"]].copy()
    d["session"] = d.index.normalize()                # server date = session
    return d

def trim_to_volume(d):
    """A volume profile needs volume. This broker back-filled XAUUSD price
    history with tick_volume=0 before 2025-12-17, so the profile is undefined
    there. Trim to the volume-populated window and report what was dropped."""
    nz = d.index[d["tick_volume"] > 0]
    if not len(nz):
        return None, {"raw": len(d), "kept": 0, "pct_zero": 100.0}
    t = d.loc[nz[0]:]
    info = {"raw": len(d), "kept": len(t),
            "pct_zero_raw": 100*(d["tick_volume"] == 0).mean(),
            "pct_zero_kept": 100*(t["tick_volume"] == 0).mean(),
            "from": t.index[0], "to": t.index[-1],
            "years": (t.index[-1]-t.index[0]).days/365.25}
    return t, info

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

def to_gmt(idx):
    """Broker clock is EET/EEST (calibrated earlier: corr 0.9998 at -3h in summer)."""
    idx = pd.DatetimeIndex(idx)
    return idx - pd.to_timedelta(np.where(eu_dst_mask(idx), 3, 2), unit="h")

# ------------------------------------------------------------------ profile
def session_profile(g):
    """VAH / VAL / POC from one session's bars using tick volume.
    Volume is spread uniformly over each bar's high-low span."""
    lo, hi = g["low"].min(), g["high"].max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo: return None
    mid = 0.5*(hi+lo)
    w = mid*BIN_BP/1e4
    if w <= 0: return None
    nb = int(np.ceil((hi-lo)/w)) + 1
    if nb < 5 or nb > 20000:
        if nb < 5: nb = 5
        else: return None
    edges = lo + w*np.arange(nb+1)
    vol = np.zeros(nb)
    for l, h, v in zip(g["low"].values, g["high"].values, g["tick_volume"].values):
        i0 = int(max(0, np.floor((l-lo)/w)))
        i1 = int(min(nb-1, np.floor((h-lo)/w)))
        if i1 < i0: i1 = i0
        vol[i0:i1+1] += v/(i1-i0+1)
    tot = vol.sum()
    if tot <= 0: return None
    poc_i = int(np.argmax(vol))
    # expand around POC, always taking the richer neighbour, to VA_PCT
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < VA_PCT*tot and (lo_i > 0 or hi_i < nb-1):
        below = vol[lo_i-1] if lo_i > 0 else -1
        above = vol[hi_i+1] if hi_i < nb-1 else -1
        if above >= below:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    ctr = lambda i: 0.5*(edges[i]+edges[i+1])
    return {"poc": ctr(poc_i), "vah": ctr(hi_i), "val": ctr(lo_i),
            "sess_hi": hi, "sess_lo": lo, "tot_vol": tot, "nbars": len(g)}

# ------------------------------------------------------------------ events
def build_events(d, bars_per_hour):
    """First upside and first downside extension per session, vs PRIOR session's
    value area. Returns a DataFrame of events with excursion measures."""
    sess = sorted(d["session"].unique())
    profs = {}
    for s in sess:
        g = d[d["session"] == s]
        if len(g) >= MIN_SESSION_BARS:
            p = session_profile(g)
            if p: profs[s] = p
    idxpos = {t: i for i, t in enumerate(d.index)}
    hi_a, lo_a = d["high"].values, d["low"].values
    tv = d["tick_volume"].values
    maxh = max(HORIZONS_H)
    rows = []
    for k in range(1, len(sess)):
        s_prev, s_cur = sess[k-1], sess[k]
        if s_prev not in profs: continue
        p = profs[s_prev]
        vah, val, poc = p["vah"], p["val"], p["poc"]
        if not (val < poc < vah): continue
        g = d[d["session"] == s_cur]
        if len(g) < 4: continue
        for side, lvl in (("up", vah), ("dn", val)):
            hit = g.index[g["high"] >= lvl] if side == "up" else g.index[g["low"] <= lvl]
            if len(hit) == 0: continue
            t0 = hit[0]
            i0 = idxpos[t0]
            # "tired auction": volume into the extension vs the preceding baseline
            f0 = max(0, i0-TIRED_FAST+1); s0 = max(0, i0-TIRED_FAST-TIRED_SLOW+1)
            fast = tv[f0:i0+1].mean() if i0 >= f0 else np.nan
            slow = tv[s0:f0].mean() if f0 > s0 else np.nan
            vratio = fast/slow if (np.isfinite(fast) and np.isfinite(slow) and slow > 0) else np.nan
            dist_poc = abs(lvl - poc)
            if dist_poc <= 0: continue
            row = {"sym_side": side, "t0": t0, "session": s_cur, "lvl": lvl,
                   "poc": poc, "vah": vah, "val": val, "dist_poc": dist_poc,
                   "px": 0.5*(vah+val), "vratio": vratio,
                   "gmt_hour": to_gmt([t0])[0].hour}
            revert_bar = None
            for H in HORIZONS_H:
                n = int(H*bars_per_hour)
                j1 = min(i0+n, len(d)-1)
                if j1 <= i0:
                    row["mfe_%d" % H] = np.nan; row["mae_%d" % H] = np.nan
                    row["rev_%d" % H] = np.nan; continue
                wh = hi_a[i0+1:j1+1]; wl = lo_a[i0+1:j1+1]
                if len(wh) == 0:
                    row["mfe_%d" % H] = np.nan; row["mae_%d" % H] = np.nan
                    row["rev_%d" % H] = np.nan; continue
                if side == "up":
                    mfe = max(lvl - wl.min(), 0.0)      # back down toward POC
                    mae = max(wh.max() - lvl, 0.0)      # further up, away
                    rev = wl.min() <= poc
                    if revert_bar is None:
                        w = np.where(wl <= poc)[0]
                        if len(w): revert_bar = w[0]+1
                else:
                    mfe = max(wh.max() - lvl, 0.0)      # back up toward POC
                    mae = max(lvl - wl.min(), 0.0)
                    rev = wh.max() >= poc
                    if revert_bar is None:
                        w = np.where(wh >= poc)[0]
                        if len(w): revert_bar = w[0]+1
                row["mfe_%d" % H] = mfe; row["mae_%d" % H] = mae
                row["rev_%d" % H] = bool(rev)
            row["revert_bars"] = revert_bar
            row["enough"] = (i0 + int(maxh*bars_per_hour)) < len(d)
            rows.append(row)
    return pd.DataFrame(rows), len(profs), len(sess)

def zprop(k1, n1, k2, n2):
    """Two-proportion z-test."""
    if min(n1, n2) < 10: return np.nan, np.nan, np.nan
    p1, p2 = k1/n1, k2/n2
    p = (k1+k2)/(n1+n2)
    se = np.sqrt(p*(1-p)*(1/n1+1/n2))
    if se <= 0: return p1-p2, np.nan, np.nan
    z = (p1-p2)/se
    return p1-p2, z, 2*0.5*np.erfc(abs(z)/np.sqrt(2)) if hasattr(np, "erfc") else np.nan

def report(sym, tfname, d, ev, nprof, nsess, bph, thin, vinfo):
    gmt = to_gmt(d.index)
    print("\n" + "#"*96)
    print("# %s  %s" % (sym, tfname))
    print("#"*96)
    print("raw bars pulled      : %d  (%.1f%% had tick_volume = 0)"
          % (vinfo["raw"], vinfo["pct_zero_raw"]))
    print("VOLUME-USABLE window : %s -> %s" % (vinfo["from"].date(), vinfo["to"].date()))
    print("  bars %d   %.2f years   sessions %d   with usable profile %d"
          % (len(d), vinfo["years"], nsess, nprof))
    print("  GMT %s -> %s" % (gmt[0], gmt[-1]))
    print("median bars/session  : %.0f" % d.groupby("session").size().median())
    if vinfo["pct_zero_raw"] > 5:
        print("\n  ! %.1f%% of the pulled history carried tick_volume = 0 and was" % vinfo["pct_zero_raw"])
        print("    DISCARDED. A value area cannot be computed without volume, so the")
        print("    price history that exists before %s is unusable for" % vinfo["from"].date())
        print("    this question no matter how deep it looks in the inventory table.")
    if thin:
        print("\n  *** THIN-DATA FLAG ***")
        print("  %.2f volume-usable years / %d sessions. Too short to support a" % (vinfo["years"], nsess))
        print("  conclusion about CONDITIONAL revert rates: the hour-of-day and")
        print("  tired-auction splits divide an already-small event count into cells")
        print("  of a few dozen, where a 10pp swing is noise. Pooled revert rate and")
        print("  MFE/MAE are the only figures here with usable precision, and even")
        print("  they cover barely one regime. Do not weigh these equally against a")
        print("  deeper sample.")
    if ev.empty:
        print("\nNo extension events built - nothing further to report.")
        return None
    ok = ev[ev["enough"]].copy()
    print("\nextension events: %d total (%d with a full 12h forward window)"
          % (len(ev), len(ok)))
    print("   upside (beyond prior VAH): %d    downside (beyond prior VAL): %d"
          % ((ok["sym_side"] == "up").sum(), (ok["sym_side"] == "dn").sum()))
    if len(ok) < 30:
        print("   too few complete events for distributional claims.")
        return None

    # ---- (2) revert-to-POC rate and timing, by horizon ----
    print("\n--- Q2: REVERT-TO-POC RATE BY HORIZON (both sides pooled) ---")
    print("%-8s %10s %10s %12s" % ("horizon", "revert%", "n", "cum median h"))
    for H in HORIZONS_H:
        c = ok["rev_%d" % H].dropna()
        if not len(c): continue
        rb = ok.loc[ok["rev_%d" % H] == True, "revert_bars"].dropna()
        med_h = (rb.median()/bph) if len(rb) else np.nan
        print("%-8s %9.1f%% %10d %12s"
              % ("%dh" % H, 100*c.mean(), len(c),
                 "%.1f" % med_h if np.isfinite(med_h) else "-"))

    # ---- (1) MFE toward POC vs MAE further out ----
    print("\n--- Q1: EXCURSION TOWARD POC (MFE) vs FURTHER OUT (MAE), in bps ---")
    print("%-8s %10s %10s %10s %10s %12s %10s"
          % ("horizon", "medMFE", "medMAE", "meanMFE", "meanMAE", "med MFE/MAE", "MFE>MAE%"))
    ratios = {}
    for H in HORIZONS_H:
        mfe = ok["mfe_%d" % H]/ok["px"]*1e4
        mae = ok["mae_%d" % H]/ok["px"]*1e4
        m = np.isfinite(mfe) & np.isfinite(mae)
        if m.sum() < 20: continue
        r = (mfe[m]/mae[m].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
        ratios[H] = r.median()
        print("%-8s %10.1f %10.1f %10.1f %10.1f %12.2f %9.1f%%"
              % ("%dh" % H, mfe[m].median(), mae[m].median(), mfe[m].mean(),
                 mae[m].mean(), r.median(), 100*(mfe[m] > mae[m]).mean()))
    print("  (MFE = distance travelled back toward POC from the level;")
    print("   MAE = distance travelled further away. Ratio > 1 means the pull")
    print("   back toward value dominated the continuation, on the median day.)")

    # ---- fraction of the way back to POC ----
    print("\n--- how far back toward POC did it get? (1.00 = reached POC) ---")
    print("%-8s %10s %10s %10s" % ("horizon", "med frac", "p75", "p90"))
    for H in HORIZONS_H:
        f = (ok["mfe_%d" % H]/ok["dist_poc"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(f) < 20: continue
        print("%-8s %10.2f %10.2f %10.2f"
              % ("%dh" % H, f.median(), f.quantile(.75), f.quantile(.90)))

    # ---- (3a) revert rate by GMT hour ----
    print("\n--- Q3a: 4h REVERT RATE BY GMT HOUR OF THE EXTENSION ---")
    print("%-8s %10s %8s" % ("GMT hr", "revert%", "n"))
    for hh in range(24):
        c = ok.loc[ok["gmt_hour"] == hh, "rev_4"].dropna()
        if len(c) < 15: continue
        print("%-8s %9.1f%% %8d" % ("%02d:00" % hh, 100*c.mean(), len(c)))
    print("  (hours with n<15 suppressed)")

    # ---- (3b) tired auction ----
    print("\n--- Q3b: TIRED AUCTION (falling tick volume into the extension) ---")
    print("  vratio = mean tickvol of last %d bars / mean of prior %d bars"
          % (TIRED_FAST, TIRED_SLOW))
    v = ok.dropna(subset=["vratio"])
    out = {}
    for H in (4, 12):
        tired = v[v["vratio"] < 1.0]["rev_%d" % H].dropna()
        energ = v[v["vratio"] >= 1.0]["rev_%d" % H].dropna()
        if len(tired) < 10 or len(energ) < 10: continue
        k1, n1 = tired.sum(), len(tired); k2, n2 = energ.sum(), len(energ)
        diff = k1/n1 - k2/n2
        p = (k1+k2)/(n1+n2); se = np.sqrt(p*(1-p)*(1/n1+1/n2))
        z = diff/se if se > 0 else np.nan
        out[H] = (100*k1/n1, 100*k2/n2, 100*diff, z, n1, n2)
        print("  %2dh: tired %.1f%% (n=%d)  vs  rising %.1f%% (n=%d)   diff %+.1f pp   z=%.2f %s"
              % (H, 100*k1/n1, n1, 100*k2/n2, n2, 100*diff, z,
                 "SIGNIFICANT" if abs(z) > 1.96 else "not significant"))
    print("\n  by vratio quartile (4h revert rate):")
    try:
        v = v.copy()
        v["q"] = pd.qcut(v["vratio"], 4, labels=["Q1 quietest", "Q2", "Q3", "Q4 loudest"])
        for q, g in v.groupby("q", observed=True):
            c = g["rev_4"].dropna()
            if len(c) < 10: continue
            print("     %-12s revert %.1f%%  n=%d  (median vratio %.2f)"
                  % (q, 100*c.mean(), len(c), g["vratio"].median()))
    except Exception as e:
        print("     quartile split unavailable: %s" % e)

    # ---- Q3c: is "tired" merely a relabelling of "session open"? ----
    # Events cluster at session open (first-touch-per-session design), and a
    # breach at the open mechanically has LOW volume vs the prior 20 bars, which
    # belong to the previous session's active hours. So vratio<1 may just mean
    # "opening hour". Control for it by dropping the modal hour and re-testing.
    print("\n--- Q3c: CONTROL - is 'tired' just 'session open'? ---")
    hc = v.groupby("gmt_hour").size()
    modal = int(hc.idxmax())
    at_open = v["gmt_hour"] == modal
    print("  modal (session-open) hour = %02d:00 GMT, %d of %d events (%.0f%%)"
          % (modal, at_open.sum(), len(v), 100*at_open.mean()))
    print("  mean vratio at open %.2f vs elsewhere %.2f   (tired-share at open %.0f%%)"
          % (v.loc[at_open, "vratio"].mean(), v.loc[~at_open, "vratio"].mean(),
             100*(v.loc[at_open, "vratio"] < 1).mean()))
    print("  4h revert at open %.1f%% (n=%d) vs elsewhere %.1f%% (n=%d)"
          % (100*v.loc[at_open, "rev_4"].mean(), at_open.sum(),
             100*v.loc[~at_open, "rev_4"].mean(), (~at_open).sum()))
    sub = v[~at_open]
    print("  re-testing tired vs rising with the open hour REMOVED:")
    for H in (4, 12):
        t = sub[sub["vratio"] < 1.0]["rev_%d" % H].dropna()
        r = sub[sub["vratio"] >= 1.0]["rev_%d" % H].dropna()
        if len(t) < 10 or len(r) < 10: continue
        k1, n1, k2, n2 = t.sum(), len(t), r.sum(), len(r)
        p = (k1+k2)/(n1+n2); se = np.sqrt(p*(1-p)*(1/n1+1/n2))
        z = (k1/n1-k2/n2)/se if se > 0 else np.nan
        print("     %2dh: tired %.1f%% (n=%d) vs rising %.1f%% (n=%d)  %+.1fpp  z=%.2f  %s"
              % (H, 100*k1/n1, n1, 100*k2/n2, n2, 100*(k1/n1-k2/n2), z,
                 "SURVIVES control" if abs(z) > 1.96 else "does NOT survive control"))

    hdr = {"revert_4h": 100*ok["rev_4"].dropna().mean(),
           "revert_12h": 100*ok["rev_12"].dropna().mean(),
           "ratio_4h": ratios.get(4, np.nan), "ratio_12h": ratios.get(12, np.nan),
           "tired": out.get(4), "n": len(ok), "thin": thin}
    return hdr

def main():
    mt5 = mt5_connect()
    ai = mt5.account_info()
    print("attached to terminal: server %s  (%s)" % (ai.server, ai.company))
    print("\n" + "="*96)
    print("DATA INVENTORY (reported before any analysis, as requested)")
    print("="*96)
    print("%-14s %-5s %9s %11s %11s %7s | %9s %11s %7s %8s"
          % ("symbol","tf","raw bars","from","to","years","vol bars","vol from","years","%zero"))
    store = {}
    for sym in SYMBOLS:
        for tf in TFS:
            d = get_rates(mt5, sym, tf)
            if d is None:
                print("%-14s %-5s %9s" % (sym, tf, "NONE")); continue
            yrs = (d.index[-1]-d.index[0]).days/365.25
            t, vi = trim_to_volume(d)
            if t is None:
                print("%-14s %-5s %9d %11s %11s %7.2f | %9s"
                      % (sym, tf, len(d), d.index[0].date(), d.index[-1].date(), yrs,
                         "NO VOLUME AT ALL"))
                continue
            print("%-14s %-5s %9d %11s %11s %7.2f | %9d %11s %7.2f %7.1f%%"
                  % (sym, tf, len(d), d.index[0].date(), d.index[-1].date(), yrs,
                     vi["kept"], vi["from"].date(), vi["years"], vi["pct_zero_raw"]))
            store[(sym, tf)] = (t, vi)
    mt5.shutdown()
    print("\nThe right-hand block is what actually constrains this study: a volume")
    print("profile needs volume, so only the volume-populated span counts.")

    summary = {}
    for (sym, tf), (d, vi) in store.items():
        bph = 4 if tf == "M15" else 2
        ev, nprof, nsess = build_events(d, bph)
        thin = (vi["years"] < THIN_YEARS) or (nsess < 200)
        summary[(sym, tf)] = report(sym, tf, d, ev, nprof, nsess, bph, thin, vi)

    print("\n" + "="*96)
    print("THE THREE NUMBERS")
    print("="*96)
    print("%-22s %12s %12s %14s %10s"
          % ("instrument/tf", "revert 4h", "revert 12h", "med MFE/MAE 4h", "tired?"))
    for k, s in summary.items():
        if not s:
            print("%-22s %12s" % ("%s %s" % k, "insufficient")); continue
        t = s["tired"]
        tired_txt = "-" if not t else ("%+.1fpp z=%.1f%s" % (t[2], t[3], "*" if abs(t[3])>1.96 else ""))
        flag = "  [THIN]" if s["thin"] else ""
        print("%-22s %11.1f%% %11.1f%% %14.2f %10s%s"
              % ("%s %s" % k, s["revert_4h"], s["revert_12h"], s["ratio_4h"], tired_txt, flag))
    print("\nDescriptive only: no entries, no exits, no backtest, no P&L.")
    print("Tick volume is a PROXY for real volume - value areas are approximate.")

if __name__ == "__main__":
    main()
