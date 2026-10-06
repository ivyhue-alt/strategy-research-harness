"""
va_migration_test.py - Value-area MIGRATION CONTINUATION: descriptive pass, then
backtest through the rolling challenge-start engine.

THESIS UNDER TEST (continuation, not reversion): price that breaks the prior
session's VAH/VAL on RISING tick volume keeps migrating rather than snapping
back to POC. Entry at the breach, stop behind the breach bar, fixed R targets.

SPEC AS GIVEN
  entry   : break of prior-session VAH (long) / VAL (short) on rising volume
  stop    : opposite side of the breach bar, or 2xATR, WHICHEVER IS TIGHTER
  targets : 1.5R and 2R variants
  primary : EURUSD M15/M30 (1.19y, 100% volume coverage)
  thin    : XAUUSD M15/M30 (0.61y usable volume) - flagged, not weighted equally
  gate    : win rate > 50%  AND  RETURN/DD > 1.5  AND  max DD < 10%

VOLUME CONDITION - NOTE THE SPEC INCONSISTENCY
  The brief says "Q4-loudest ... i.e. take the side that did NOT revert". Those
  point opposite ways: Q4 (rising vol) reverted 40-47%, so ~55% continued, while
  Q1 (quietest) reverted only 12-21%, so 79-88% continued. Q4 is therefore the
  WORSE quartile by non-reversion. Q4 is run as PRIMARY exactly as specified
  (rising volume = conviction behind the migration is a coherent thesis in its
  own right); Q1 is run as a labelled SECONDARY because the brief's own stated
  rationale implies it. Both face the identical gate.

CHOICES I HAD TO MAKE (not in the brief - flagged so they are not mistaken for
findings):
  * TIME EXIT of 12h. Without one the test is undefined; 12h matches the window
    used in the descriptive study that motivated this.
  * vratio thresholds Q4 >= 1.5, Q1 <= 0.35, read off the descriptive quartile
    medians. THIS IS IN-SAMPLE SELECTION on a 1.19y sample - the first/second
    half stability split is reported precisely because of it.
  * Entry at the breach bar's CLOSE (bar-close execution, as in prior work).

STANDING RULES HONOURED: pessimistic intrabar (stop wins ties), 1% risk off
CURRENT equity, one position at a time, no look-ahead in the profile (it is
built from the PRIOR session only).

RUN (MT5 terminal LOGGED IN):  python scripts/va_migration_test.py
"""
import os, sys
import numpy as np
import pandas as pd

import volume_profile_research as V   # reuse validated profile + data helpers

PRIMARY = [("EURUSD.sim", "M15"), ("EURUSD.sim", "M30")]
THIN    = [("XAUUSD.sim", "M15"), ("XAUUSD.sim", "M30")]

ATR_N = 14
ATR_MULT = 2.0
RISK = 0.01
START_BAL = 10_000.0
HOLD_HOURS = 12
TARGETS = [1.5, 2.0]
VOL_CONDS = {"Q4-rising(primary)": ("ge", 1.5), "Q1-quiet(secondary)": ("le", 0.35)}

# spread assumptions (round trip = 2x half). EURUSD ~1 pip, gold ~5.3bp half
# per the earlier account reading. Sensitivity is reported for the headline cell.
HALF_SPREAD_PCT = {"EURUSD.sim": 0.0040, "XAUUSD.sim": 0.0053}
SENSITIVITY = [0.5, 1.0, 2.0]      # multipliers on the half-spread

# FTMO 2-Step, same engine as the portfolio work
P1_TARGET, P2_TARGET = 0.10, 0.05
DAILY_LOSS, MAX_LOSS = 0.05, 0.10
START_EVERY_H = 24                 # test a new challenge start every 24h of bars

def wilder(s, n): return s.ewm(alpha=1.0/n, adjust=False).mean()

def add_atr(d):
    hi, lo, cl = d["high"], d["low"], d["close"]
    prev = cl.shift(1)
    tr = pd.concat([hi-lo, (hi-prev).abs(), (lo-prev).abs()], axis=1).max(axis=1)
    d = d.copy(); d["atr"] = wilder(tr, ATR_N)
    return d

# ------------------------------------------------------------------ signals
def build_signals(d, bph):
    """One signal per session per side: first breach of the PRIOR session's
    VAH/VAL. Carries vratio (volume into the breach) and the bar's own low/high
    for the stop. No look-ahead: the profile uses only the previous session."""
    sess = sorted(d["session"].unique())
    profs = {}
    for s in sess:
        g = d[d["session"] == s]
        if len(g) >= V.MIN_SESSION_BARS:
            p = V.session_profile(g)
            if p: profs[s] = p
    pos = {t: i for i, t in enumerate(d.index)}
    tv = d["tick_volume"].values
    sigs = []
    for k in range(1, len(sess)):
        sp, sc = sess[k-1], sess[k]
        if sp not in profs: continue
        p = profs[sp]
        if not (p["val"] < p["poc"] < p["vah"]): continue
        g = d[d["session"] == sc]
        if len(g) < 4: continue
        for side, lvl in (("long", p["vah"]), ("short", p["val"])):
            hit = g.index[g["high"] >= lvl] if side == "long" else g.index[g["low"] <= lvl]
            if len(hit) == 0: continue
            t0 = hit[0]; i0 = pos[t0]
            f0 = max(0, i0-V.TIRED_FAST+1); s0 = max(0, i0-V.TIRED_FAST-V.TIRED_SLOW+1)
            fast = tv[f0:i0+1].mean() if i0 >= f0 else np.nan
            slow = tv[s0:f0].mean() if f0 > s0 else np.nan
            if not (np.isfinite(fast) and np.isfinite(slow) and slow > 0): continue
            r = d.iloc[i0]
            if not np.isfinite(r["atr"]) or r["atr"] <= 0: continue
            sigs.append({"i": i0, "t": t0, "side": side, "lvl": lvl, "poc": p["poc"],
                         "vratio": fast/slow, "close": r["close"],
                         "bar_low": r["low"], "bar_high": r["high"], "atr": r["atr"],
                         "session": sc})
    return pd.DataFrame(sigs)

def stop_for(sig, entry):
    """Opposite side of the breach bar, or 2xATR, whichever is TIGHTER."""
    if sig["side"] == "long":
        return max(sig["bar_low"], entry - ATR_MULT*sig["atr"])   # higher = tighter
    return min(sig["bar_high"], entry + ATR_MULT*sig["atr"])      # lower  = tighter

# ------------------------------------------------------------------ descriptive
def descriptive(d, sigs, bph, half_pct, label):
    """What happens to these signals BEFORE any position sizing or equity path:
    does the target get hit before the stop? This predicts the backtest win rate."""
    n = int(HOLD_HOURS*bph)
    hi, lo = d["high"].values, d["low"].values
    rows = []
    for _, s in sigs.iterrows():
        i0 = int(s["i"]); sp = s["close"]*half_pct/100.0
        entry = s["close"] + sp if s["side"] == "long" else s["close"] - sp
        stop = stop_for(s, entry)
        R = abs(entry-stop)
        if R <= 0 or R < 2*sp: continue
        j1 = min(i0+n, len(d)-1)
        if j1 <= i0: continue
        wh, wl = hi[i0+1:j1+1], lo[i0+1:j1+1]
        if not len(wh): continue
        if s["side"] == "long":
            mfe = (wh.max()-entry)/R; mae = (entry-wl.min())/R
        else:
            mfe = (entry-wl.min())/R; mae = (wh.max()-entry)/R
        rec = {"mfe_R": mfe, "mae_R": mae, "R": R, "side": s["side"]}
        # pessimistic first-touch: stop wins ties inside a bar
        for T in TARGETS:
            outcome, bars = "time", len(wh)
            for b in range(len(wh)):
                if s["side"] == "long":
                    hit_s = wl[b] <= stop; hit_t = wh[b] >= entry + T*R
                else:
                    hit_s = wh[b] >= stop; hit_t = wl[b] <= entry - T*R
                if hit_s: outcome, bars = "stop", b+1; break
                if hit_t: outcome, bars = "target", b+1; break
            rec["out_%.1f" % T] = outcome; rec["bars_%.1f" % T] = bars
        rows.append(rec)
    if not rows: return None
    r = pd.DataFrame(rows)
    print("\n  -- descriptive (pre-backtest): %s  n=%d --" % (label, len(r)))
    print("     median MFE %.2fR   median MAE %.2fR   MFE>MAE in %.0f%% of signals"
          % (r["mfe_R"].median(), r["mae_R"].median(), 100*(r["mfe_R"] > r["mae_R"]).mean()))
    for T in TARGETS:
        o = r["out_%.1f" % T].value_counts()
        w = o.get("target", 0); l = o.get("stop", 0); tm = o.get("time", 0)
        tot = w+l+tm
        print("     %.1fR: target-first %d (%.1f%%)  stop-first %d (%.1f%%)  timed-out %d (%.1f%%)"
              % (T, w, 100*w/tot, l, 100*l/tot, tm, 100*tm/tot))
    return r

# ------------------------------------------------------------------ backtest
def backtest(d, sigs, bph, target_R, half_pct):
    """One position at a time, 1% risk off CURRENT equity, pessimistic intrabar."""
    n = int(HOLD_HOURS*bph)
    hi, lo, cl = d["high"].values, d["low"].values, d["close"].values
    eq = START_BAL
    curve = np.full(len(d), np.nan)
    trades = []
    open_pos = None
    sig_by_i = {int(s["i"]): s for _, s in sigs.iterrows()}
    for i in range(len(d)):
        # ---- manage open position (stop checked first: pessimistic) ----
        if open_pos is not None:
            p = open_pos
            done = False
            if p["side"] == "long":
                if lo[i] <= p["stop"]: fill, res = p["stop"], "stop"; done = True
                elif hi[i] >= p["tgt"]: fill, res = p["tgt"], "target"; done = True
            else:
                if hi[i] >= p["stop"]: fill, res = p["stop"], "stop"; done = True
                elif lo[i] <= p["tgt"]: fill, res = p["tgt"], "target"; done = True
            if not done and i >= p["expire"]:
                fill, res = cl[i], "time"; done = True
            if done:
                sp = fill*half_pct/100.0
                exit_px = fill - sp if p["side"] == "long" else fill + sp
                pnl = p["qty"]*(exit_px - p["entry"]) if p["side"] == "long" \
                      else p["qty"]*(p["entry"] - exit_px)
                eq += pnl
                trades.append({"t": d.index[i], "res": res, "pnl": pnl,
                               "R": pnl/(p["risk_cash"]) if p["risk_cash"] else np.nan})
                open_pos = None
        # ---- new entry ----
        if open_pos is None and i in sig_by_i:
            s = sig_by_i[i]
            sp = s["close"]*half_pct/100.0
            entry = s["close"] + sp if s["side"] == "long" else s["close"] - sp
            stop = stop_for(s, entry)
            R = abs(entry-stop)
            if R > 0 and R >= 2*sp:
                risk_cash = eq*RISK
                qty = risk_cash/R
                tgt = entry + target_R*R if s["side"] == "long" else entry - target_R*R
                open_pos = {"side": s["side"], "entry": entry, "stop": stop, "tgt": tgt,
                            "qty": qty, "expire": i+n, "risk_cash": risk_cash}
        # ---- mark to market ----
        m = eq
        if open_pos is not None:
            p = open_pos
            m += p["qty"]*(cl[i]-p["entry"]) if p["side"] == "long" \
                 else p["qty"]*(p["entry"]-cl[i])
        curve[i] = m
    return pd.Series(curve, index=d.index).dropna(), pd.DataFrame(trades)

def challenge_from(curve, i0):
    start = curve.iloc[i0]; phase, target, base = 1, P1_TARGET, start
    day, day_open = None, start
    for i in range(i0, len(curve)):
        ts, v = curve.index[i], curve.iloc[i]
        if day != ts.date(): day, day_open = ts.date(), v
        if v <= base*(1-MAX_LOSS): return "FAIL_MAXLOSS"
        if v <= day_open - base*DAILY_LOSS: return "FAIL_DAILY"
        if v >= base*(1+target):
            if phase == 2: return "PASS"
            phase, target, base = 2, P2_TARGET, v
    return "TIMEOUT"

def evaluate(curve, trades, bph, label):
    if trades.empty or len(curve) < 50:
        print("     %s: no trades" % label); return None
    ret = curve.iloc[-1]/START_BAL - 1
    dd = (curve/curve.cummax()-1).min()
    yrs = max((curve.index[-1]-curve.index[0]).days/365.25, .1)
    cagr = (1+ret)**(1/yrs)-1
    wins = (trades["pnl"] > 0).sum()
    wr = 100*wins/len(trades)
    rdd = abs(cagr/dd) if dd else 0.0
    step = int(START_EVERY_H*bph)
    res = [challenge_from(curve, i) for i in range(0, max(len(curve)-100, 1), step)]
    oc = pd.Series(res).value_counts()
    passed = (wr > 50) and (rdd > 1.5) and (abs(dd) < 0.10)
    return {"label": label, "ret": ret, "cagr": cagr, "dd": dd, "rdd": rdd,
            "n": len(trades), "wr": wr, "pass_rate": 100*oc.get("PASS", 0)/len(res),
            "gate": passed, "outcomes": oc, "curve": curve, "trades": trades}

def stability(curve, trades):
    """First half vs second half - the in-sample threshold selection makes this
    the minimum honest robustness check."""
    if trades.empty: return "-"
    mid = curve.index[len(curve)//2]
    a = trades[trades["t"] <= mid]; b = trades[trades["t"] > mid]
    if len(a) < 10 or len(b) < 10: return "too few"
    return "win%% %.0f -> %.0f (n %d/%d)" % (100*(a["pnl"] > 0).mean(),
                                             100*(b["pnl"] > 0).mean(), len(a), len(b))

# ------------------------------------------------------------------ main
def main():
    try:
        mt5 = V.mt5_connect()
    except SystemExit as e:
        print("!! %s" % e)
        print("!! The MT5 terminal is running but not logged in, so no data can be")
        print("!! pulled. Log the terminal into an account and re-run - the engine")
        print("!! below is already built and self-validated.")
        sys.exit(1)
    store = {}
    print("="*100)
    print("DATA INVENTORY (volume-usable window is what constrains this study)")
    print("="*100)
    for sym, tf in PRIMARY + THIN:
        d = V.get_rates(mt5, sym, tf)
        if d is None: print("%-12s %-4s NONE" % (sym, tf)); continue
        t, vi = V.trim_to_volume(d)
        if t is None: print("%-12s %-4s NO VOLUME" % (sym, tf)); continue
        print("%-12s %-4s raw %6d -> vol-usable %6d bars  %s..%s  %.2fy  (%.0f%% zero dropped)"
              % (sym, tf, vi["raw"], vi["kept"], vi["from"].date(), vi["to"].date(),
                 vi["years"], vi["pct_zero_raw"]))
        store[(sym, tf)] = (add_atr(t), vi)
    mt5.shutdown()

    results = []
    for (sym, tf) in PRIMARY + THIN:
        if (sym, tf) not in store: continue
        d, vi = store[(sym, tf)]
        bph = 4 if tf == "M15" else 2
        half = HALF_SPREAD_PCT[sym]
        thin = (sym, tf) in THIN
        sigs_all = build_signals(d, bph)
        print("\n" + "#"*100)
        print("# %s %s   %.2f volume-usable years   %d raw signals%s"
              % (sym, tf, vi["years"], len(sigs_all), "   *** THIN ***" if thin else ""))
        print("#"*100)
        if thin:
            print("  THIN FLAG: 0.61y of volume history. Reported for completeness;")
            print("  do NOT weigh against the 1.19y EURUSD result.")
        if sigs_all.empty: print("  no signals"); continue
        for cname, (op, thr) in VOL_CONDS.items():
            s = sigs_all[sigs_all["vratio"] >= thr] if op == "ge" \
                else sigs_all[sigs_all["vratio"] <= thr]
            print("\n  === %s (vratio %s %.2f): %d signals ===" % (cname, op, thr, len(s)))
            if len(s) < 20:
                print("     fewer than 20 signals - not evaluated"); continue
            descriptive(d, s, bph, half, cname)
            for T in TARGETS:
                curve, tr = backtest(d, s, bph, T, half)
                r = evaluate(curve, tr, bph, "%s %s %s %.1fR" % (sym, tf, cname, T))
                if r:
                    r.update({"sym": sym, "tf": tf, "cond": cname, "T": T, "thin": thin,
                              "stab": stability(curve, tr)})
                    results.append(r)
                    print("     %.1fR: trades %d  win %.1f%%  ret %.1f%%  DD %.1f%%  "
                          "RET/DD %.2f  challenge-pass %.0f%%  %s"
                          % (T, r["n"], r["wr"], 100*r["ret"], 100*r["dd"], r["rdd"],
                             r["pass_rate"], "GATE PASS" if r["gate"] else "gate fail"))
                    print("        stability: %s" % r["stab"])

    # ---- sensitivity on the best primary cell ----
    prim = [r for r in results if not r["thin"]]
    if prim:
        best = max(prim, key=lambda r: r["rdd"])
        print("\n" + "="*100)
        print("SPREAD SENSITIVITY on the best PRIMARY cell: %s" % best["label"])
        print("="*100)
        d, vi = store[(best["sym"], best["tf"])]
        bph = 4 if best["tf"] == "M15" else 2
        sa = build_signals(d, bph)
        op, thr = VOL_CONDS[best["cond"]]
        s = sa[sa["vratio"] >= thr] if op == "ge" else sa[sa["vratio"] <= thr]
        for m in SENSITIVITY:
            h = HALF_SPREAD_PCT[best["sym"]]*m
            c, t = backtest(d, s, bph, best["T"], h)
            r = evaluate(c, t, bph, "x%.1f" % m)
            if r: print("  half-spread x%.1f (%.4f%%): win %.1f%%  ret %.1f%%  DD %.1f%%  RET/DD %.2f  %s"
                        % (m, h, r["wr"], 100*r["ret"], 100*r["dd"], r["rdd"],
                           "PASS" if r["gate"] else "fail"))

    # ---- verdict ----
    print("\n" + "="*100)
    print("GATE: win rate > 50%  AND  RETURN/DD > 1.5  AND  max DD < 10%")
    print("="*100)
    print("%-46s %7s %7s %8s %8s %7s %6s" %
          ("cell", "trades", "win%", "ret%", "maxDD%", "RET/DD", "GATE"))
    for r in results:
        print("%-46s %7d %6.1f%% %7.1f%% %7.1f%% %7.2f %6s%s"
              % (r["label"], r["n"], r["wr"], 100*r["ret"], 100*r["dd"], r["rdd"],
                 "PASS" if r["gate"] else "fail", "  [THIN]" if r["thin"] else ""))
    pp = [r for r in results if r["gate"] and not r["thin"]]
    print("\nPRIMARY (EURUSD) cells passing all three gate conditions: %d of %d"
          % (len(pp), len([r for r in results if not r["thin"]])))
    print("Cells tested: %d. At 5%% each, ~%.1f would clear a p<0.05 bar by chance;"
          % (len(results), 0.05*len(results)))
    print("the gate is stricter than that, but treat a single passing cell among")
    print("many as weak evidence, not a result.")

if __name__ == "__main__":
    main()
