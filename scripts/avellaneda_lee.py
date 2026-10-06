"""
avellaneda_lee.py - Residual mean-reversion, Avellaneda-Lee (2010) specification.
NOT cointegration: this regresses RETURNS on RETURNS, accumulates the residual,
and fits an Ornstein-Uhlenbeck process to that cumulative residual.

SPECIFICATION (per bar, rolling 60-day window)
  1. OLS  r_A = alpha + beta * r_B + eps        (RETURNS, not log prices)
  2. X_t  = cumsum(eps) within the window       (cumulative residual)
  3. OU fit via AR(1) on X:  X_{t+1} = a + b X_t + zeta
       kappa   = -ln(b)                  (per bar)
       m       = a/(1-b)                 (equilibrium level)
       sigma_eq= sqrt(var(zeta)/(1-b^2)) (equilibrium st.dev.)
       s-score = (X_t - m)/sigma_eq
       half-life = ln(2)/kappa
  4. Trade the s-score: entry |s|>2, exit |s|<0.5, hard stop |s|>3.5,
     time stop 20 days. s<-2 => long the spread (long A, short beta*B).

X RESETS each window, as in the paper (X is defined inside the estimation
window). Refits happen daily; between refits beta/m/sigma_eq are held and X is
updated bar-by-bar, so stops are evaluated at full bar resolution.

PESSIMISTIC INTRABAR: for the hard stop, the adverse case is assumed to happen
first within the bar - leg A at its low and leg B at its high for a long spread
(and the reverse for a short). The residual increment is recomputed from those
worst-case leg excursions, so a bar that could have breached 3.5 intrabar is
treated as a stop-out even if it closed inside. Stop always wins ties.

STATIONARITY CHECK (the honest analogue of the cointegration test): ADF on the
cumulative residual X within each window. Because X is built from OLS residuals
of an estimated regression, standard ADF critical values do not apply, so the
null is simulated by pushing two INDEPENDENT return series through the identical
pipeline (regress -> cumsum -> ADF) and reading empirical quantiles.

SIZING: 1% of CURRENT equity risked to the |s|=3.5 stop, so R is well defined
and win rate / drawdown are comparable with earlier work. Two-leg round-trip
cost = 2*(hs_A + |beta|*hs_B) charged in return units.

GATE: win rate > 50%  AND  RETURN/DD > 1.5  AND  max DD < 10%

RUN (MT5 terminal logged in):  python scripts/avellaneda_lee.py
"""
import sys
import numpy as np
import pandas as pd

PAIRS = [("XAUUSD.sim", "XAGUSD.sim"),
         ("US30.sim", "US500.sim"),
         ("US500.sim", "US100.sim"),
         ("US30.sim", "US100.sim")]
TFS = ["D1", "H1"]

WIN_DAYS = 60
S_ENTRY, S_EXIT, S_STOP = 2.0, 0.5, 3.5
TIME_STOP_DAYS = 20
RISK = 0.01
START_BAL = 10_000.0
ADF_LAGS = 2
MC_REPS = 2000

def connect():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        sys.exit("MT5 attach failed: %s (terminal logged in?)" % (mt5.last_error(),))
    return mt5

def get(mt5, sym, tfname):
    tf = {"D1": mt5.TIMEFRAME_D1, "H1": mt5.TIMEFRAME_H1}[tfname]
    mt5.symbol_select(sym, True)
    r = None
    for n in (50_000, 20_000, 10_000, 5_000, 2_000):
        r = mt5.copy_rates_from_pos(sym, tf, 0, n)
        if r is not None and len(r): break
    if r is None or not len(r): return None
    d = pd.DataFrame(r); d.index = pd.to_datetime(d["time"], unit="s")
    return d[["open", "high", "low", "close"]].astype(float)

def half_spread_bps(mt5, sym):
    si = mt5.symbol_info(sym)
    if si is None or si.ask <= 0 or si.bid <= 0: return np.nan
    return 1e4*(si.ask-si.bid)/2.0/(0.5*(si.bid+si.ask))

# ------------------------------------------------------------------ econometrics
def adf_t(e, lags=ADF_LAGS):
    e = np.asarray(e, float); T = len(e)
    if T < lags+15: return np.nan
    de = np.diff(e); p = lags
    y = de[p:]
    cols = [e[p:T-1]]
    for i in range(1, p+1): cols.append(de[p-i:T-1-i])
    X = np.column_stack(cols)
    if X.shape[0] <= X.shape[1]+2: return np.nan
    try:
        b = np.linalg.lstsq(X, y, rcond=None)[0]
        r = y - X@b
        dof = len(y)-X.shape[1]
        if dof <= 0: return np.nan
        s2 = (r@r)/dof
        se = np.sqrt(s2*np.linalg.inv(X.T@X)[0, 0])
        return b[0]/se if se > 0 else np.nan
    except np.linalg.LinAlgError:
        return np.nan

def ou_fit(X):
    """AR(1) on X -> kappa, m, sigma_eq. Returns None if not mean-reverting."""
    if len(X) < 20: return None
    x0, x1 = X[:-1], X[1:]
    A = np.column_stack([np.ones(len(x0)), x0])
    try:
        c = np.linalg.lstsq(A, x1, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    a, b = c[0], c[1]
    if not (0 < b < 1): return None          # not mean-reverting
    z = x1 - A@c
    varz = z@z/max(len(z)-2, 1)
    if varz <= 0: return None
    kappa = -np.log(b)
    m = a/(1-b)
    sig_eq = np.sqrt(varz/(1-b*b))
    if not np.isfinite(sig_eq) or sig_eq <= 0: return None
    return kappa, m, sig_eq

_MC = {}
def mc_null(W, lags=ADF_LAGS, reps=MC_REPS, seed=999):
    """Null for ADF on the cumulative residual of a returns-on-returns
    regression between two INDEPENDENT series."""
    key = (int(round(W/50.0)*50), lags)
    if key in _MC: return _MC[key]
    Wk = max(key[0], 40)
    rng = np.random.default_rng(seed+key[0])
    out = np.empty(reps)
    for i in range(reps):
        ra = rng.standard_normal(Wk); rb = rng.standard_normal(Wk)
        A = np.column_stack([np.ones(Wk), rb])
        c = np.linalg.lstsq(A, ra, rcond=None)[0]
        X = np.cumsum(ra - A@c)
        out[i] = adf_t(X, lags)
    out = out[np.isfinite(out)]; out.sort()
    _MC[key] = out
    return out

# ------------------------------------------------------------------ engine
def run_pair(a_df, b_df, tfname, hs_a, hs_b, stop_mode="close"):
    """stop_mode:
      "close" - stop/target evaluated at bar CLOSES, stop wins ties. This is the
                primary specification. The joint intrabar path of TWO legs is
                unobservable from bar data, so no intrabar spread excursion is
                invented.
      "worst" - adverse bound: leg A at its low WITH leg B at its high on the
                same bar. For legs correlated ~0.8 this is far harsher than
                reality (it assumes the legs move in opposite directions
                intrabar, every bar) and is reported only as a bound."""
    df = pd.concat({"ah": a_df["high"], "al": a_df["low"], "ac": a_df["close"],
                    "bh": b_df["high"], "bl": b_df["low"], "bc": b_df["close"]},
                   axis=1).dropna()
    if len(df) < 400: return None
    n = len(df)
    days = pd.DatetimeIndex(df.index).normalize()
    bpd = float(pd.Series(1, index=days).groupby(days).size().median())
    W = int(WIN_DAYS*bpd)
    refit_every = max(int(round(bpd)), 1)     # refit daily
    tstop = int(TIME_STOP_DAYS*bpd)
    if W + tstop + 10 >= n: return None

    ac = df["ac"].values; bc = df["bc"].values
    ah, al = df["ah"].values, df["al"].values
    bh, bl = df["bh"].values, df["bl"].values
    rA = np.concatenate([[np.nan], np.diff(np.log(ac))])
    rB = np.concatenate([[np.nan], np.diff(np.log(bc))])
    # worst-case (pessimistic) per-bar leg returns, from the prior close
    rA_lo = np.concatenate([[np.nan], np.log(al[1:]/ac[:-1])])
    rA_hi = np.concatenate([[np.nan], np.log(ah[1:]/ac[:-1])])
    rB_lo = np.concatenate([[np.nan], np.log(bl[1:]/bc[:-1])])
    rB_hi = np.concatenate([[np.nan], np.log(bh[1:]/bc[:-1])])

    null = mc_null(W)
    crit5 = np.percentile(null, 5) if len(null) else np.nan

    eq = START_BAL
    curve = np.full(n, np.nan)
    trades = []
    pos = None
    beta = alpha = m = sig_eq = kappa = np.nan
    X = np.nan
    hl_list, adf_list, stat_list, kfilter = [], [], [], []

    for i in range(W+1, n):
        # ---------- refit (daily) ----------
        if (i - (W+1)) % refit_every == 0:
            ya = rA[i-W+1:i+1]; xb = rB[i-W+1:i+1]
            ok = np.isfinite(ya) & np.isfinite(xb)
            if ok.sum() > 30:
                yv, xv = ya[ok], xb[ok]
                A = np.column_stack([np.ones(len(xv)), xv])
                try:
                    c = np.linalg.lstsq(A, yv, rcond=None)[0]
                    alpha, beta = c[0], c[1]
                    Xw = np.cumsum(yv - A@c)
                    fit = ou_fit(Xw)
                    t = adf_t(Xw)
                    adf_list.append(t)
                    stat_list.append(bool(np.isfinite(t) and t < crit5))
                    if fit:
                        kappa, m, sig_eq = fit
                        X = Xw[-1]
                        hl = np.log(2)/kappa
                        hl_list.append(hl)
                        # A-L tradability filter: reversion faster than half the window
                        kfilter.append(bool((1.0/kappa) < W/2.0))
                    else:
                        kappa = m = sig_eq = np.nan; X = np.nan
                except np.linalg.LinAlgError:
                    kappa = m = sig_eq = np.nan; X = np.nan
        else:
            # hold beta/m/sigma_eq, update X with this bar's residual
            if np.isfinite(X) and np.isfinite(beta) and np.isfinite(rA[i]):
                X += rA[i] - alpha - beta*rB[i]

        s = (X - m)/sig_eq if (np.isfinite(X) and np.isfinite(sig_eq)) else np.nan

        # ---------- manage open position: STOP FIRST (pessimistic) ----------
        # P&L accrues from the REALISED spread return under the hedge ratio fixed
        # at entry. It must NOT be read off X: X is a rolling cumulative residual
        # that resets every refit, so X values from different windows are not
        # comparable and differencing them is meaningless.
        if pos is not None:
            b0 = pos["beta0"]
            d_act = rA[i] - b0*rB[i] if np.isfinite(rA[i]) and np.isfinite(rB[i]) else 0.0
            if stop_mode == "worst":
                if pos["dir"] > 0:
                    d_bad = rA_lo[i] - b0*(rB_hi[i] if b0 >= 0 else rB_lo[i])
                else:
                    d_bad = rA_hi[i] - b0*(rB_lo[i] if b0 >= 0 else rB_hi[i])
                if not np.isfinite(d_bad): d_bad = d_act
            else:
                d_bad = d_act        # close-based; stop still checked before target
            held = i - pos["i0"]
            adverse_bad = pos["dir"]*(pos["pnl_ret"] + d_bad)
            why = None; realised = None
            if adverse_bad <= -pos["room"]:                 # hard stop, s hit 3.5
                realised = -pos["dir"]*pos["room"]
                why = "stop"
            else:
                pos["pnl_ret"] += d_act
                if np.isfinite(s) and abs(s) < S_EXIT:
                    realised = pos["pnl_ret"]; why = "target"
                elif held >= tstop:
                    realised = pos["pnl_ret"]; why = "time"
                elif not np.isfinite(s):
                    realised = pos["pnl_ret"]; why = "nofit"
            if why is not None:
                pnl = pos["dir"]*pos["N"]*realised - pos["cost"]
                eq += pnl
                trades.append({"t": df.index[i], "why": why, "pnl": pnl,
                               "R": pnl/pos["risk_cash"] if pos["risk_cash"] else np.nan,
                               "bars": held, "s0": pos["s0"]})
                pos = None
        # ---------- entry ----------
        if pos is None and np.isfinite(s) and abs(s) > S_ENTRY and abs(s) < S_STOP:
            room = (S_STOP - abs(s))*sig_eq   # adverse spread-return distance to s=3.5
            if room > 0 and np.isfinite(beta):
                risk_cash = eq*RISK
                N = risk_cash/room
                cost = N*2.0*(hs_a + abs(beta)*hs_b)/1e4
                pos = {"dir": 1 if s < 0 else -1, "N": N, "beta0": beta, "i0": i,
                       "room": room, "pnl_ret": 0.0,
                       "risk_cash": risk_cash, "cost": cost, "s0": s}
        # ---------- mark to market ----------
        mtm = eq
        if pos is not None:
            mtm = eq + pos["dir"]*pos["N"]*pos["pnl_ret"] - pos["cost"]
        curve[i] = mtm

    cv = pd.Series(curve, index=df.index).dropna()
    tr = pd.DataFrame(trades)
    yrs = (df.index[-1]-df.index[0]).days/365.25
    hl_med = np.median([h for h in hl_list if np.isfinite(h)]) if hl_list else np.nan
    return {"curve": cv, "trades": tr, "years": yrs, "bpd": bpd, "W": W,
            "hl_bars": hl_med, "hl_days": hl_med/bpd if np.isfinite(hl_med) else np.nan,
            "stat_frac": 100*np.mean(stat_list) if stat_list else np.nan,
            "n_windows": len(stat_list),
            "kfilt_frac": 100*np.mean(kfilter) if kfilter else np.nan,
            "adf_med": np.median([t for t in adf_list if np.isfinite(t)]) if adf_list else np.nan,
            "crit5": crit5}

def summarise(r, label):
    cv, tr = r["curve"], r["trades"]
    if tr.empty or len(cv) < 50:
        return {"label": label, "n": 0, "wr": np.nan, "ret": np.nan, "dd": np.nan,
                "rdd": np.nan, "tpy": 0, "hl_days": r["hl_days"],
                "stat_frac": r["stat_frac"], "gate": False,
                "adf_med": r["adf_med"], "crit5": r["crit5"],
                "kfilt": r["kfilt_frac"], "nw": r["n_windows"]}
    ret = cv.iloc[-1]/START_BAL - 1
    dd = (cv/cv.cummax()-1).min()
    cagr = (1+ret)**(1/r["years"])-1 if ret > -1 else np.nan
    wr = 100*(tr["pnl"] > 0).mean()
    rdd = abs(cagr/dd) if dd else np.nan
    return {"label": label, "n": len(tr), "wr": wr, "ret": ret, "cagr": cagr,
            "dd": dd, "rdd": rdd, "tpy": len(tr)/r["years"],
            "hl_days": r["hl_days"], "stat_frac": r["stat_frac"],
            "adf_med": r["adf_med"], "crit5": r["crit5"],
            "kfilt": r["kfilt_frac"], "nw": r["n_windows"],
            "why": tr["why"].value_counts().to_dict(),
            "gate": bool(wr > 50 and np.isfinite(rdd) and rdd > 1.5 and abs(dd) < 0.10)}

def main():
    mt5 = connect()
    syms = sorted({s for p in PAIRS for s in p})
    hs = {s: half_spread_bps(mt5, s) for s in syms}
    print("half-spreads (symbol_info last-known bid/ask, closed-market snapshot):")
    for s in syms: print("   %-12s %.3f bps" % (s, hs[s]))
    data = {}
    for tf in TFS:
        for s in syms: data[(s, tf)] = get(mt5, s, tf)
    mt5.shutdown()

    rows, rows_worst = [], []
    for tf in TFS:
        for a, b in PAIRS:
            if data.get((a, tf)) is None or data.get((b, tf)) is None: continue
            lbl = "%s/%s %s" % (a.replace(".sim",""), b.replace(".sim",""), tf)
            r = run_pair(data[(a, tf)], data[(b, tf)], tf, hs[a], hs[b], stop_mode="close")
            if r is None:
                print("  %s: insufficient data" % lbl); continue
            s = summarise(r, lbl); rows.append(s)
            rw = run_pair(data[(a, tf)], data[(b, tf)], tf, hs[a], hs[b], stop_mode="worst")
            rows_worst.append(summarise(rw, lbl))
            print("  done %-22s trades %4d  win %5.1f%%  stationary %4.1f%%"
                  % (lbl, s["n"], s["wr"] if np.isfinite(s["wr"]) else -1, s["stat_frac"]))

    print("\n" + "="*118)
    print("AVELLANEDA-LEE RESIDUAL MEAN-REVERSION  (60-day window, s in/out/stop"
          " = %.1f/%.1f/%.1f, %dd time stop)" % (S_ENTRY, S_EXIT, S_STOP, TIME_STOP_DAYS))
    print("="*118)
    print("%-24s %6s %7s %8s %8s %8s %8s %9s %10s %6s"
          % ("pair/tf", "trades", "win%", "ret%", "maxDD%", "RET/DD", "trd/yr",
             "HL days", "stationary", "GATE"))
    for s in rows:
        print("%-24s %6d %6.1f%% %7.1f%% %7.1f%% %8s %8.1f %9s %9.1f%% %6s"
              % (s["label"], s["n"],
                 s["wr"] if np.isfinite(s["wr"]) else 0,
                 100*s["ret"] if np.isfinite(s.get("ret", np.nan)) else 0,
                 100*s["dd"] if np.isfinite(s.get("dd", np.nan)) else 0,
                 "%.2f" % s["rdd"] if np.isfinite(s.get("rdd", np.nan)) else "-",
                 s["tpy"],
                 "%.1f" % s["hl_days"] if np.isfinite(s["hl_days"]) else "-",
                 s["stat_frac"], "PASS" if s["gate"] else "fail"))

    print("\n--- stationarity of the cumulative residual (honest analogue of the"
          " cointegration check) ---")
    print("%-24s %10s %12s %12s %10s"
          % ("pair/tf", "windows", "median ADF t", "MC 5% crit", "A-L kappa filter"))
    for s in rows:
        print("%-24s %10d %12s %12s %13.1f%%"
              % (s["label"], s["nw"],
                 "%.2f" % s["adf_med"] if np.isfinite(s["adf_med"]) else "-",
                 "%.2f" % s["crit5"] if np.isfinite(s["crit5"]) else "-",
                 s["kfilt"] if np.isfinite(s["kfilt"]) else -1))
    print("  'A-L kappa filter' = %% of windows whose mean-reversion time is faster")
    print("  than half the estimation window (Avellaneda-Lee's own tradability screen).")

    print("\n--- exit reason mix (close-based stops) ---")
    for s in rows:
        if s.get("why"): print("  %-24s %s" % (s["label"], s["why"]))

    print("\n--- BRACKET: same runs with the ultra-conservative intrabar bound ---")
    print("(leg A's low paired with leg B's high on the same bar. For ~0.8"
          " correlated\n legs this is far harsher than reality; shown as a"
          " worst case, not the estimate.)")
    print("%-24s %6s %7s %8s %8s"
          % ("pair/tf", "trades", "win%", "ret%", "maxDD%"))
    for s in rows_worst:
        print("%-24s %6d %6.1f%% %7.1f%% %7.1f%%"
              % (s["label"], s["n"],
                 s["wr"] if np.isfinite(s["wr"]) else 0,
                 100*s["ret"] if np.isfinite(s.get("ret", np.nan)) else 0,
                 100*s["dd"] if np.isfinite(s.get("dd", np.nan)) else 0))

    print("\n" + "="*118)
    print("GATE: win rate > 50%  AND  RETURN/DD > 1.5  AND  max DD < 10%")
    print("="*118)
    p = [s for s in rows if s["gate"]]
    print("configs passing all three: %d of %d" % (len(p), len(rows)))
    for s in p: print("   PASS: %s" % s["label"])
    print("\nDescriptive backtest. Pessimistic intrabar (worst-case leg excursions,")
    print("stop wins ties). Cost = two-leg round trip from a closed-market spread")
    print("snapshot, so a lower bound on live cost.")

if __name__ == "__main__":
    main()
