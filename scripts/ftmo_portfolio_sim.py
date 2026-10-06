"""
ftmo_portfolio_sim.py - The test that decides FTMO viability.

Everything before this measured symbols in isolation. FTMO evaluates the ACCOUNT.
This runs all symbols on ONE balance and answers: what fraction of possible
challenge start dates would have PASSED?

FIXES to my earlier harness:
  - sizing is fixed-fractional off CURRENT equity (was: starting balance)
  - pessimistic intrabar (stop wins ties)
  - shared position cap and one shared balance across symbols

DESIGN: instead of one backtest, it simulates starting a challenge on every
Nth bar and walks FTMO's real rules forward from there. That gives a pass RATE
from correlated real data, not an assumed-independence Monte Carlo.

RUN (MT5 terminal open):  python scripts/ftmo_portfolio_sim.py
"""
import os, sys
import numpy as np
import pandas as pd

# BREADTH TEST (test A, scoped to what this account holds).
#   NARROW3 = the original 3 (the current live config).
#   LONG7   = every symbol with long (2022-09) H4 history. Nominally 7, but
#             effective breadth ~4 independent bets: US30/US500/US100 are ~0.9+
#             correlated (one equity-index bet), gold+silver move together
#             (one metals bet), plus oil and BTC. Read as a DIRECTION indicator.
#   FULL    = everything available; only exists on the short (2025-05) window.
NARROW3 = ["XAUUSD.sim", "US100.sim", "BTCUSD.sim"]
LONG7   = ["XAUUSD.sim", "XAGUSD.sim", "USOIL.sim", "BTCUSD.sim",
           "US30.sim", "US500.sim", "US100.sim"]
FX_REST = ["EURUSD.sim", "GBPUSD.sim", "USDJPY.sim", "AUDUSD.sim", "USDCAD.sim",
           "USDCHF.sim", "NZDUSD.sim", "EURGBP.sim", "EURJPY.sim", "GBPJPY.sim",
           "AUDJPY.sim", "EURAUD.sim", "EURCHF.sim", "AUDNZD.sim", "USDSGD.sim",
           "USDZAR.sim", "USDMXN.sim", "USDNOK.sim", "USDSEK.sim", "XAGUSD.sim"]
ALL_SYMS = list(dict.fromkeys(LONG7 + NARROW3 + FX_REST))   # pulled from MT5

DON_ENTRY, DON_EXIT, ADX_MIN = 20, 55, 20.0
ATR_N = ADX_N = 14
ATR_MULT = 2.0
RISK = 0.01                 # 1% of CURRENT equity
MAX_OPEN = 3                # risk-architecture (3->2) tested and closed: hurt RETURN/DD, DD unchanged
SAME_BAR_BLOCK = True       # <=1 new entry per bar, portfolio-wide (validated neutral on 3 symbols)
START_BAL = 10_000.0
ORDER_SEEDS = [1, 2, 3, 4]  # random symbol orderings, to measure luck-of-the-list dispersion

# FTMO 2-Step
P1_TARGET, P2_TARGET = 0.10, 0.05
DAILY_LOSS, MAX_LOSS = 0.05, 0.10
MIN_DAYS = 4
START_EVERY = 30            # test a new challenge start every N bars

def load_env(p=".env.mt5"):
    for ln in open(p):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def wilder(s, n): return s.ewm(alpha=1.0/n, adjust=False).mean()

def prep(df):
    hi, lo, cl = df["high"], df["low"], df["close"]
    prev = cl.shift(1)
    tr = pd.concat([hi-lo, (hi-prev).abs(), (lo-prev).abs()], axis=1).max(axis=1)
    d = df.copy()
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

def portfolio_run(data, spreads, max_open, same_bar_block=SAME_BAR_BLOCK,
                  alloc="list_order", order=None, corr=None):
    """One shared balance across symbols. Market entry at breakout-bar close.

    alloc decides WHO gets a scarce slot when more signals fire than we can take:
      "list_order" - iteration order (arbitrary; pass `order` to permute it)
      "adx"        - highest ADX wins the slot (the pre-authorised tie-break)

    Returns (equity, trades, stats dict) where stats carries both the contention
    accounting and EFFECTIVE-BREADTH measures (position-bar concentration and
    average pairwise correlation of the live book) so we can tell whether nominal
    breadth was ever actually exercised.
    """
    idx = sorted(set().union(*[set(d.index) for d in data.values()]))
    syms = list(order) if order else list(data.keys())
    eq = START_BAL
    pos = {}             # sym -> dict(qty, entry, stop)
    curve, trades = {}, []
    # contention accounting, split into the two distinct phenomena:
    #   order_contested = a slot was FREE and >1 signal competed, so the tie-break
    #                     rule actually PICKED the winner (the confound).
    #   capacity_full   = book already full, signals dropped uniformly; order is
    #                     irrelevant that bar (normal saturation, not a bias).
    bars_with_signal = order_contested_bars = capacity_full_bars = 0
    signals_lost_to_order = entries_taken = 0
    # effective-breadth accounting
    pos_bars = {s: 0 for s in data}     # bars each symbol actually HELD a position
    book_corrs = []                     # avg pairwise corr of the book, per bar with >=2 open
    concurrent = []
    for ts in idx:
        # ---- manage open positions first (pessimistic: stop before exit) ----
        for sym in list(pos.keys()):
            d = data[sym]
            if ts not in d.index: continue
            r = d.loc[ts]; p = pos[sym]; sp = spreads[sym]
            long = p["qty"] > 0
            hit = (r["low"] <= p["stop"]) if long else (r["high"] >= p["stop"])
            if hit:
                fill = p["stop"]*(1-sp) if long else p["stop"]*(1+sp)
            elif (long and r["close"] <= r["exit_lo"]) or (not long and r["close"] >= r["exit_hi"]):
                fill = r["close"]*(1-sp) if long else r["close"]*(1+sp)
            else:
                continue
            pnl = p["qty"]*(fill - p["entry"])
            eq += pnl
            trades.append({"ts": ts, "sym": sym, "pnl": pnl})
            del pos[sym]

        # ---- collect all eligible entry signals this bar ----
        cands = []   # (sym, dir, px, dist, sp, adx) in iteration order
        for sym in syms:
            d = data[sym]
            if sym in pos or ts not in d.index: continue
            r = d.loc[ts]
            if r["adx"] < ADX_MIN: continue
            dist = ATR_MULT*r["atr"]
            if dist <= 0: continue
            sp = spreads[sym]; px = r["close"]
            if px > r["don_hi"]:
                cands.append((sym, 1, px, dist, sp, r["adx"]))
            elif px < r["don_lo"]:
                cands.append((sym, -1, px, dist, sp, r["adx"]))

        # ---- how many we can actually act on, and contention bookkeeping ----
        slots_free = max(max_open - len(pos), 0)
        max_take = min(slots_free, 1) if same_bar_block else slots_free
        take = min(len(cands), max_take)
        if cands:
            bars_with_signal += 1
            if max_take == 0:
                capacity_full_bars += 1                 # book full; order irrelevant
            elif len(cands) > take:
                order_contested_bars += 1               # slot free AND >1 competed
                signals_lost_to_order += len(cands) - take

        # ---- allocate scarce slots by the chosen rule ----
        if alloc == "adx":
            cands = sorted(cands, key=lambda c: -c[5])  # strongest trend wins
        for sym, dr, px, dist, sp, _adx in cands[:take]:
            if dr == 1:
                pos[sym] = {"qty": (eq*RISK)/dist, "entry": px*(1+sp), "stop": px*(1+sp)-dist}
            else:
                pos[sym] = {"qty": -(eq*RISK)/dist, "entry": px*(1-sp), "stop": px*(1-sp)+dist}
            entries_taken += 1

        # ---- mark to market ----
        mtm = eq
        for sym, p in pos.items():
            d = data[sym]
            if ts in d.index:
                mtm += p["qty"]*(d.loc[ts, "close"] - p["entry"])
        curve[ts] = mtm

        # ---- effective-breadth sampling ----
        held = list(pos.keys())
        for s in held: pos_bars[s] += 1
        concurrent.append(len(held))
        if corr is not None and len(held) >= 2:
            vals = [abs(corr.loc[a, b]) for i, a in enumerate(held) for b in held[i+1:]
                    if a in corr.index and b in corr.columns and pd.notna(corr.loc[a, b])]
            if vals: book_corrs.append(float(np.mean(vals)))
    # ---- effective breadth: Herfindahl on position-bar shares. If one symbol
    # hogs the slots, eff_N collapses toward 1 no matter how many are nominally
    # in the universe. This is the "was breadth ever exercised?" number.
    tot_pb = sum(pos_bars.values())
    if tot_pb:
        shares = np.array([v/tot_pb for v in pos_bars.values() if v > 0])
        eff_n = float(1.0/np.sum(shares**2))
        top = sorted(pos_bars.items(), key=lambda kv: -kv[1])[:3]
        top_str = ",".join("%s %.0f%%" % (k.replace(".sim",""), 100*v/tot_pb) for k, v in top if v)
    else:
        eff_n, top_str = 0.0, "-"
    stats = {
        "bars_with_signal": bars_with_signal, "entries_taken": entries_taken,
        "order_contested_bars": order_contested_bars,
        "capacity_full_bars": capacity_full_bars,
        "signals_lost_to_order": signals_lost_to_order,
        # the confound magnitude: how often the tie-break rule chose a winner
        "order_contested_pct": 100*order_contested_bars/bars_with_signal if bars_with_signal else 0.0,
        "capacity_full_pct": 100*capacity_full_bars/bars_with_signal if bars_with_signal else 0.0,
        # effective breadth
        "n_held": sum(1 for v in pos_bars.values() if v > 0),
        "eff_breadth": eff_n,
        "top_holders": top_str,
        "avg_book_corr": float(np.mean(book_corrs)) if book_corrs else float("nan"),
        "avg_concurrent": float(np.mean(concurrent)) if concurrent else 0.0,
    }
    return pd.Series(curve, name="equity"), trades, stats

def challenge_from(curve, i0):
    """Walk FTMO 2-Step forward from bar i0. Returns (result, bars_used)."""
    start = curve.iloc[i0]
    phase, target = 1, P1_TARGET
    base = start                      # phase base equity
    day = None; day_open = start
    for i in range(i0, len(curve)):
        ts, v = curve.index[i], curve.iloc[i]
        if day != ts.date():
            day = ts.date(); day_open = v
        if v <= base*(1-MAX_LOSS): return "FAIL_MAXLOSS", i-i0
        if v <= day_open - base*DAILY_LOSS: return "FAIL_DAILY", i-i0
        if v >= base*(1+target):
            if phase == 2: return "PASS", i-i0
            phase, target, base = 2, P2_TARGET, v
    return "TIMEOUT", len(curve)-i0

def get(mt5, sym, n=6000):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 1, n)
    if r is None or len(r) < 400: return None, None
    d = pd.DataFrame(r); d.index = pd.to_datetime(d["time"], unit="s")
    t = mt5.symbol_info_tick(sym)
    mid = (t.bid+t.ask)/2 if t and t.bid else float(d["close"].iloc[-1])
    sp = ((t.ask-t.bid)/2/mid) if t and t.ask > t.bid else 0.0002
    return d[["open","high","low","close"]], sp

def compute_stats(curve, trades):
    ret = curve.iloc[-1]/START_BAL - 1
    dd = (curve/curve.cummax() - 1).min()
    yrs = max((curve.index[-1]-curve.index[0]).days/365.25, 0.25)
    cagr = (1+ret)**(1/yrs) - 1
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    res = [challenge_from(curve, i) for i in range(0, len(curve)-100, START_EVERY)]
    outcomes = pd.Series([r[0] for r in res]).value_counts()
    passes = [r[1] for r in res if r[0] == "PASS"]
    return {
        "window": "%s -> %s (%.1f yrs)" % (curve.index[0].date(), curve.index[-1].date(), yrs),
        "return": ret, "cagr": cagr, "dd": dd,
        "return_dd": abs(cagr/dd) if dd else 0.0,
        "n_trades": len(pnls),
        "win_pct": 100*len(wins)/len(pnls) if pnls else 0.0,
        "n_starts": len(res), "outcomes": outcomes,
        "median_bars_to_pass": float(np.median(passes)) if passes else None,
    }

def report(name, labeled_stats):
    """labeled_stats: list of (label, stats_dict), one per MAX_OPEN value compared."""
    labels = [lbl for lbl, _ in labeled_stats]
    col = max(10, max(len(l) for l in labels) + 2)
    fmt_row = "%-22s" + ("%" + str(col) + "s") * len(labels)
    print("\n=== %s ===" % name)
    print("window        : %s" % labeled_stats[0][1]["window"])
    print(fmt_row % tuple(["metric"] + labels))

    def row(label, valfmt, key, scale=1):
        print(fmt_row % tuple([label] + [valfmt % (s[key]*scale) for _, s in labeled_stats]))

    row("total return %", "%.2f", "return", 100)
    row("CAGR %", "%.2f", "cagr", 100)
    row("max drawdown %", "%.2f", "dd", 100)
    row("RETURN/DD", "%.2f", "return_dd")
    row("trades", "%d", "n_trades")
    row("win %", "%.1f", "win_pct")
    row("challenge starts", "%d", "n_starts")
    all_outcomes = sorted(set().union(*[set(s["outcomes"].index) for _, s in labeled_stats]))
    for oc in all_outcomes:
        cells = []
        for _, s in labeled_stats:
            v = int(s["outcomes"].get(oc, 0))
            pct = 100*v/s["n_starts"] if s["n_starts"] else 0
            cells.append("%d(%.0f%%)" % (v, pct))
        print(fmt_row % tuple([oc] + cells))
    cells = []
    for _, s in labeled_stats:
        m = s["median_bars_to_pass"]
        cells.append("%.0f" % m if m is not None else "n/a")
    print(fmt_row % tuple(["median bars to pass"] + cells))
    # ---- slot-contention confound (breadth test) ----
    print(fmt_row % tuple(["-- slot contention --"] + [""]*len(labels)))
    row("symbols in universe", "%d", "n_symbols")
    row("entries taken", "%d", "entries_taken")
    row("order-contested %", "%.1f", "order_contested_pct")   # tie-break chose winner
    row("entries lost to order", "%d", "signals_lost_to_order")
    row("book-full (satur.) %", "%.1f", "capacity_full_pct")  # book full, order irrelevant
    # ---- EFFECTIVE breadth: was nominal breadth ever exercised? ----
    print(fmt_row % tuple(["-- effective breadth --"] + [""]*len(labels)))
    row("symbols ever held", "%d", "n_held")
    row("EFF BREADTH (1/HHI)", "%.2f", "eff_breadth")
    row("avg book corr", "%.2f", "avg_book_corr")
    row("avg concurrent pos", "%.2f", "avg_concurrent")
    print(fmt_row % tuple(["top holders (pos-bars)"] + [""]*len(labels)))
    for lbl, s in labeled_stats:
        print("   %-19s %s" % (lbl, s.get("top_holders", "-")))

def pairwise_corr(raw, symbols, start=None):
    """Abs pairwise correlation of H4 close-to-close returns, for book-correlation."""
    cl = {}
    for s in symbols:
        if s not in raw: continue
        c = raw[s]["close"]
        cl[s] = (c.loc[start:] if start is not None else c).pct_change()
    if len(cl) < 2: return None
    return pd.DataFrame(cl).corr()

def run_universe(raw, spreads, symbols, start=None, max_open=MAX_OPEN,
                 alloc="list_order", order=None, corr=None):
    """Run one named universe. If start given, align all members to that bar
    (for apples-to-apples short-window comparison)."""
    data = {s: raw[s] for s in symbols if s in raw}
    if not data: return None
    s0 = start or max(d.index[0] for d in data.values())
    data = {s: d.loc[s0:] for s, d in data.items()}
    curve, trades, extra = portfolio_run(data, spreads, max_open, alloc=alloc,
                                        order=order, corr=corr)
    st = compute_stats(curve, trades)
    st.update(extra)
    st["n_symbols"] = len(data)
    return curve, trades, st

def dispersion_block(raw, spreads, symbols, start=None, corr=None):
    """Random-order runs + ADX tie-break, side by side. Answers: how much of the
    result is luck-of-the-list, and does a real tie-break rule change the verdict?"""
    cols = []
    base = run_universe(raw, spreads, symbols, start=start, corr=corr)
    cols.append(("list-order", base[2]))
    for seed in ORDER_SEEDS:
        rng = np.random.default_rng(seed)
        perm = list(symbols); rng.shuffle(perm)
        r = run_universe(raw, spreads, symbols, start=start, order=perm, corr=corr)
        cols.append(("rand%d" % seed, r[2]))
    radx = run_universe(raw, spreads, symbols, start=start, alloc="adx", corr=corr)
    cols.append(("ADX-tiebreak", radx[2]))
    return cols, radx

def main():
    load_env()
    import MetaTrader5 as mt5
    if not mt5.initialize(os.environ["MT5_TERMINAL"], login=int(os.environ["MT5_LOGIN"]),
                          password=os.environ["MT5_PASSWORD"], server=os.environ["MT5_SERVER"]):
        sys.exit("MT5 init failed: %s" % (mt5.last_error(),))
    raw, spreads = {}, {}
    for sym in ALL_SYMS:
        d, sp = get(mt5, sym)
        if d is None:
            print(sym, "- insufficient data, skipped"); continue
        raw[sym] = prep(d); spreads[sym] = sp
        print("%-12s %5d bars  %s -> %s" % (sym, len(d), d.index[0].date(), d.index[-1].date()))
    mt5.shutdown()

    narrow = [s for s in NARROW3 if s in raw]
    wide   = [s for s in LONG7 if s in raw]
    full   = list(raw.keys())

    # baseline: the 3-symbol live config, for reference in every table
    rn = run_universe(raw, spreads, narrow, corr=pairwise_corr(raw, narrow))

    # ---- PRIMARY: long window, 7-sym. Order dispersion + ADX tie-break. DECIDES ----
    cw = pairwise_corr(raw, wide)
    cols, radx = dispersion_block(raw, spreads, wide, corr=cw)
    report("LONG WINDOW (3.8y) 7-SYM: order dispersion vs ADX tie-break  <-- DECIDES",
           [("3sym-base", rn[2])] + cols)
    radx[0].to_csv("portfolio_equity_long_7sym_adx.csv")

    # ---- SUPPORTING: short window, full universe (FX at scale) ----
    if len(full) > len(wide):
        short_start = max(d.index[0] for d in raw.values())   # ~2025-05, FX-limited
        cf = pairwise_corr(raw, full, start=short_start)
        cols2, radx2 = dispersion_block(raw, spreads, full, start=short_start, corr=cf)
        report("SHORT WINDOW (1.1y) %d-SYM: order dispersion vs ADX (supporting, noisy)"
               % len(full), cols2)
        radx2[0].to_csv("portfolio_equity_short_full_adx.csv")

    # ---- the three numbers, isolated ----
    print("\n" + "="*66)
    print("THE THREE NUMBERS (long window, 7-sym):")
    rr = [s for lbl, s in cols if lbl.startswith("rand")]
    print("  RETURN/DD      list-order %.2f | ADX %.2f | random range %.2f-%.2f" % (
        cols[0][1]["return_dd"], radx[2]["return_dd"],
        min(s["return_dd"] for s in rr), max(s["return_dd"] for s in rr)))
    print("  MAX DD %%        list-order %.1f | ADX %.1f | random range %.1f..%.1f" % (
        cols[0][1]["dd"]*100, radx[2]["dd"]*100,
        min(s["dd"] for s in rr)*100, max(s["dd"] for s in rr)*100))
    print("  EFF BREADTH    list-order %.2f | ADX %.2f  (of %d nominal)" % (
        cols[0][1]["eff_breadth"], radx[2]["eff_breadth"], len(wide)))
    print("  (3-sym baseline: RETURN/DD %.2f, DD %.1f%%, eff breadth %.2f)" % (
        rn[2]["return_dd"], rn[2]["dd"]*100, rn[2]["eff_breadth"]))
    print("="*66)
    print("GATE: ADX RETURN/DD > 1.2 AND DD < ~26%% -> breadth live, do histdata FX.")
    print("Else, or if DD stays inflated across ALL orderings -> breadth and FTMO's")
    print("concurrency cap are structurally at odds -> option B (park it).")

if __name__ == "__main__":
    main()
