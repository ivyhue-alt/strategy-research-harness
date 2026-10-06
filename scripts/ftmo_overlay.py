"""
ftmo_overlay.py — FTMO US rule simulator for backtest equity curves.

Import it from any backtest script in this folder.
Feed it an equity curve (pandas Series, datetime index, equity in $)
from any backtest run. It replays FTMO US rules and tells you whether
that run would have PASSED, FAILED, or survived-without-target.

Rules encoded (verified ftmo.oanda.com, July 2026 — re-verify before paying):
  2-Step Phase 1: target +10%, daily loss 5%, max loss 10% STATIC, min 4 trading days
  2-Step Phase 2: target +5%,  daily loss 5%, max loss 10% STATIC, min 4 trading days
  1-Step:         target +10%, daily loss 3%, max loss 10% EOD-TRAILING,
                  Best Day Rule: best day's profit must be <= 50% of total profit at pass

Daily loss basis: FTMO confirmed (support, 2026-07-29) that BOTH the daily loss
and the max loss are EQUITY-based, including floating P&L on open positions -
not balance. Max loss on 2-Step is 10% of the INITIAL balance and STATIC; the
EoD-trailing variant applies to the 1-Step only.

Day rollover is midnight PRAGUE time (CET/CEST). Handled DST-aware by
to_prague() / INDEX_TZ below - a fixed offset is right only half the year and a
wrong boundary invents phantom daily-loss breaches.

Usage:
    import pandas as pd
    from ftmo_overlay import run_all_plans

    eq = pd.Series(equity_values, index=timestamps)  # from your backtest
    print(run_all_plans(eq, account_size=100_000))
"""

import numpy as np
import pandas as pd

# FTMO confirmed (support, 2026-07-29): the trading day rolls over at MIDNIGHT
# PRAGUE time, i.e. CET (UTC+1) in winter and CEST (UTC+2) in summer. A fixed
# offset is therefore correct for only half the year, and a wrong day boundary
# manufactures phantom daily-loss breaches. This is now DST-aware.
#
# INDEX_TZ says what clock the equity curve's index is already on:
#   "SERVER_EET" - MT5 broker server time (EET/EEST = UTC+2/+3). This project's
#                  curves come from MT5 bars, so this is the default. EET and
#                  Prague shift on the SAME EU dates, so the offset is a
#                  constant -1h all year.
#   "UTC"        - genuine UTC timestamps; needs +1h winter / +2h summer.
INDEX_TZ = "SERVER_EET"


def _eu_dst_mask(idx) -> np.ndarray:
    """EU summer time: last Sunday in March 01:00 UTC -> last Sunday in Oct 01:00 UTC."""
    idx = pd.DatetimeIndex(idx)
    out = np.zeros(len(idx), dtype=bool)
    for y in sorted(set(idx.year)):
        mar = pd.Timestamp(year=y, month=3, day=31)
        while mar.dayofweek != 6:
            mar -= pd.Timedelta(days=1)
        oct_ = pd.Timestamp(year=y, month=10, day=31)
        while oct_.dayofweek != 6:
            oct_ -= pd.Timedelta(days=1)
        out |= ((idx >= mar + pd.Timedelta(hours=1))
                & (idx < oct_ + pd.Timedelta(hours=1)))
    return out


def to_prague(idx) -> pd.DatetimeIndex:
    """Shift an index onto Prague wall-clock time so that .date() gives the
    correct FTMO trading day on both sides of the DST boundary."""
    idx = pd.DatetimeIndex(idx)
    if INDEX_TZ == "SERVER_EET":
        return idx - pd.Timedelta(hours=1)      # EET/EEST -> CET/CEST, constant
    if INDEX_TZ == "UTC":
        return idx + pd.to_timedelta(np.where(_eu_dst_mask(idx), 2, 1), unit="h")
    raise ValueError("INDEX_TZ must be 'SERVER_EET' or 'UTC', got %r" % INDEX_TZ)


def _daily_frames(equity: pd.Series):
    """Group equity curve into FTMO trading days (midnight Prague)."""
    return equity.groupby(to_prague(equity.index).date)


def simulate(equity: pd.Series, account_size: float, profit_target_pct: float,
             daily_loss_pct: float, max_loss_pct: float, trailing: bool,
             min_days: int = 0, best_day_rule: bool = False) -> dict:
    """
    equity: Series of account equity in $ at each bar close, scaled to account_size.
            If your backtest ran on a different capital base, rescale first:
            eq_scaled = account_size * (eq / eq.iloc[0])
    Returns dict with result + breach details + headroom stats.
    """
    eq = account_size * (equity / equity.iloc[0])  # normalize defensively
    start = account_size
    target = start * (1 + profit_target_pct)
    static_floor = start * (1 - max_loss_pct)

    trail_floor = static_floor          # for trailing: floor moves up at EOD
    day_pnls = []                       # closed daily P&L for Best Day Rule
    min_daily_headroom = float("inf")   # worst intraday proximity to daily limit
    min_max_headroom = float("inf")     # worst proximity to max-loss floor
    result = None

    for day, day_eq in _daily_frames(eq):
        day_start_ref = max(day_eq.iloc[0], day_pnls_balance(day_pnls, start))
        daily_floor = day_start_ref - start * daily_loss_pct
        active_floor = trail_floor if trailing else static_floor

        for ts, val in day_eq.items():
            min_daily_headroom = min(min_daily_headroom, val - daily_floor)
            min_max_headroom = min(min_max_headroom, val - active_floor)
            if val <= daily_floor:
                result = {"result": "FAILED", "rule": "daily_loss",
                          "when": str(ts), "equity": round(val, 2)}
                break
            if val <= active_floor:
                result = {"result": "FAILED", "rule": "max_loss",
                          "when": str(ts), "equity": round(val, 2)}
                break
            if val >= target and len(day_pnls) + 1 >= min_days:
                if best_day_rule and day_pnls:
                    total = val - start
                    best = max(day_pnls + [val - day_eq.iloc[0]])
                    if total > 0 and best > 0.5 * total:
                        continue  # target hit but Best Day Rule blocks; keep trading
                result = {"result": "PASSED", "rule": "profit_target",
                          "when": str(ts), "equity": round(val, 2)}
                break
        if result:
            break

        eod = day_eq.iloc[-1]
        day_pnls.append(eod - (day_eq.iloc[0]))
        if trailing:
            trail_floor = max(trail_floor, eod - start * 0.10)  # EOD trailing 10%

    if result is None:
        final = eq.iloc[-1]
        result = {"result": "SURVIVED_NO_TARGET", "rule": None,
                  "when": str(eq.index[-1]), "equity": round(final, 2)}

    result.update({
        "days_traded": len(day_pnls) + (0 if result["rule"] else 0),
        "worst_daily_headroom_$": round(min_daily_headroom, 2),
        "worst_maxloss_headroom_$": round(min_max_headroom, 2),
        "final_return_pct": round((eq.iloc[-1] / start - 1) * 100, 2),
    })
    return result


def day_pnls_balance(day_pnls, start):
    """Balance at start of current day = start + sum of closed daily P&L."""
    return start + sum(day_pnls)


def run_all_plans(equity: pd.Series, account_size: float = 100_000) -> pd.DataFrame:
    plans = {
        "2step_phase1": dict(profit_target_pct=0.10, daily_loss_pct=0.05,
                             max_loss_pct=0.10, trailing=False, min_days=4),
        "2step_phase2": dict(profit_target_pct=0.05, daily_loss_pct=0.05,
                             max_loss_pct=0.10, trailing=False, min_days=4),
        "1step":        dict(profit_target_pct=0.10, daily_loss_pct=0.03,
                             max_loss_pct=0.10, trailing=True, min_days=0,
                             best_day_rule=True),
    }
    rows = {name: simulate(equity, account_size, **p) for name, p in plans.items()}
    return pd.DataFrame(rows).T


if __name__ == "__main__":
    # Smoke test with synthetic curve
    import numpy as np
    ts = pd.date_range("2026-06-01", periods=30 * 24, freq="h")
    rng = np.random.default_rng(7)
    eq = pd.Series(100_000 * np.cumprod(1 + rng.normal(0.0002, 0.004, len(ts))), index=ts)
    print(run_all_plans(eq))
