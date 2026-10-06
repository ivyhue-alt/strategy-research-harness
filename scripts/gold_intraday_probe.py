"""
gold_intraday_probe.py - STEP 0: data inventory + timezone/session diagnosis.

Before any statistics we must know, empirically:
  - how much M30 history the broker actually holds for XAUUSD
  - where the daily maintenance break sits (that defines the "trading day")
  - the server->GMT offset, DST-aware, validated against the break location
No statistics, no strategy. Diagnosis only.
"""
import os, sys, glob
from datetime import datetime, timezone
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

def eu_dst(ts):
    """True if timestamp is in EU summer time (last Sun Mar 01:00 UTC -> last Sun Oct 01:00 UTC)."""
    y = ts.year
    mar = pd.Timestamp(year=y, month=3, day=31)
    while mar.dayofweek != 6: mar -= pd.Timedelta(days=1)
    mar += pd.Timedelta(hours=1)
    oct_ = pd.Timestamp(year=y, month=10, day=31)
    while oct_.dayofweek != 6: oct_ -= pd.Timedelta(days=1)
    oct_ += pd.Timedelta(hours=1)
    return mar <= ts < oct_

def main():
    load_env()
    import MetaTrader5 as mt5
    if not mt5.initialize(os.environ["MT5_TERMINAL"], login=int(os.environ["MT5_LOGIN"]),
                          password=os.environ["MT5_PASSWORD"], server=os.environ["MT5_SERVER"]):
        sys.exit("MT5 init failed: %s" % (mt5.last_error(),))
    mt5.symbol_select(SYM, True)

    # ---- how much M30 history is really available? ----
    got = None
    for n in (500_000, 200_000, 100_000, 50_000):
        r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M30, 0, n)
        if r is not None and len(r):
            got = pd.DataFrame(r); break
    if got is None: sys.exit("no M30 data")
    got["dt"] = pd.to_datetime(got["time"], unit="s")   # server clock, naive
    print("M30 bars available : %d" % len(got))
    print("M30 range (server) : %s -> %s" % (got["dt"].iloc[0], got["dt"].iloc[-1]))
    print("span               : %.2f years" % ((got["dt"].iloc[-1]-got["dt"].iloc[0]).days/365.25))

    # ---- server -> UTC offset, from the live tick vs true UTC ----
    t = mt5.symbol_info_tick(SYM)
    si = mt5.symbol_info(SYM)
    srv_now = pd.to_datetime(t.time, unit="s")
    utc_now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    off_now = round((srv_now - utc_now).total_seconds()/3600)
    print("\nserver now         : %s" % srv_now)
    print("utc now            : %s" % utc_now)
    print("server offset now  : UTC%+d  (EU DST now = %s)" % (off_now, eu_dst(utc_now)))
    print("implied winter off : UTC%+d" % (off_now - (1 if eu_dst(utc_now) else 0)))

    # ---- live spread, to sanity-check the 0.0053% half-spread figure ----
    mid = (t.bid + t.ask)/2
    print("\nbid/ask            : %.3f / %.3f   mid %.3f" % (t.bid, t.ask, mid))
    print("half-spread        : %.5f%%   round-trip %.5f%%"
          % (100*(t.ask-t.bid)/2/mid, 100*(t.ask-t.bid)/mid))
    print("symbol spread(pts) : %s   digits %s   point %s"
          % (si.spread, si.digits, si.point))
    mt5.shutdown()

    # ---- where is the daily break? bars per server half-hour ----
    got["hhmm"] = got["dt"].dt.strftime("%H:%M")
    cnt = got.groupby("hhmm").size()
    print("\n--- M30 bar count per SERVER half-hour (lowest 6 = the break) ---")
    for k, v in cnt.nsmallest(6).items(): print("   %s  %5d" % (k, v))
    print("   median across slots: %d" % int(cnt.median()))

    # ---- same diagnosis for the HistData M1 files ----
    files = sorted(glob.glob(os.path.join(M1_DIR, "*.csv")))
    print("\n--- HistData M1 files ---")
    m1 = []
    for f in files:
        d = pd.read_csv(f, header=None,
                        names=["d","t","open","high","low","close","vol"])
        d["dt"] = pd.to_datetime(d["d"]+" "+d["t"], format="%Y.%m.%d %H:%M")
        m1.append(d)
        print("   %s  %6d rows  %s -> %s"
              % (os.path.basename(f), len(d), d["dt"].iloc[0], d["dt"].iloc[-1]))
    m1 = pd.concat(m1, ignore_index=True).sort_values("dt")
    m1["hh"] = m1["dt"].dt.hour
    c1 = m1.groupby("hh").size()
    print("   M1 rows per file-hour (lowest 4 = break):")
    for k, v in c1.nsmallest(4).items(): print("      %02d:00  %5d" % (k, v))
    print("   -> break at file-hour %d:00 implies file clock = ET (COMEX 17:00 halt)"
          % c1.idxmin())

if __name__ == "__main__":
    main()
