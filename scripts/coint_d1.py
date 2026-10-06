"""
coint_d1.py - D1 cointegration test. SINGLE CHANGE from the M30/H1 study:
timeframe D1 with 250-day and 500-day rolling windows. The econometric engine in
coint_research.py is untouched and still carries its validation (Monte Carlo EG
null matched published asymptotics to +/-0.03; ADF separates AR(0.95) at -8.32
from a random walk at -1.55).

RATIONALE: high-frequency noise can swamp a slow equilibrium. M30/H1 found
cointegration holding in only 1-9% of windows with median EG t of -1.5 to -2.3.
If a genuine long-run relationship exists, D1 over multi-year windows is where
it should surface.

GATE (pre-committed)
  * cointegration holding in a MAJORITY of windows at 5%
  * median EG statistic below -3.34
  * half-life under ~20 days
All three must clear.

NOTE ON HALF-LIFE UNITS: at D1 one bar is one TRADING day, so half-life "bars"
are trading days (~5 per calendar week).

RUN (MT5 terminal logged in):  python scripts/coint_d1.py
"""
import numpy as np
import coint_research as C

C.TFS = ["D1"]
C.WINDOWS_D = [250, 500]
C.STEP_D = 10          # 10 trading days between window starts

def main():
    print("#"*100)
    print("# D1 COINTEGRATION TEST - single change from the M30/H1 study")
    print("# windows: 250 and 500 TRADING days, step 10 days")
    print("# GATE: majority of windows hold at 5%%  AND  median EG t < -3.34"
          "  AND  half-life < ~20 days")
    print("#"*100)
    C.main()
    print("\n" + "#"*100)
    print("# GATE READ-OUT")
    print("#"*100)
    print("Compare against the M30/H1 baseline that already failed:")
    print("  M30/H1 hold@5%% ranged 1.0-9.0%%, median EG t -1.49 to -2.34.")
    print("A majority-holding result at D1 with EG t < -3.34 and half-life < 20d")
    print("would mean the relationship exists but was drowned in intraday noise.")
    print("Anything resembling the M30/H1 numbers closes the pair family too.")

if __name__ == "__main__":
    main()
