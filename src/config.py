"""Paths and strategy parameters.

All tuned constants live here. Modify them to explore other configurations.
"""
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "results")

os.makedirs(DATA, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- universe
INDEX_UNIVERSE = 800          # point-in-time top-N by market-cap proxy
MIN_SW_INDUSTRY_SIZE = 6      # keep SW level-2 buckets with >=6 names
MIN_TRADED_DAYS = 495         # out of a 504-day formation window (no ffill)

# ---------------------------------------------------------------- pairs
FORMATION_DAYS = 504          # 2 years
TEST_DAYS = 126               # last 6 months of the formation window = OOS test
TRAIN_DAYS = 378              # first 18 months = beta estimation
EG_SCREEN_P = 0.20            # Engle-Granger pre-screen (speed only)
JOHANSEN_LEVEL = 0.95         # det_order=0, k_ar_diff=1
ADF_FULL_MAX = 0.05           # full-window ADF p-value gate
ADF_TEST_MAX = 0.10           # two-stage out-of-sample ADF p-value gate
MCAP_RATIO_MAX = 1.0          # |ln(mcap_A/mcap_B)| < this

# ---------------------------------------------------------------- portfolio
TARGET_PAIRS = 8
MAX_PER_INDUSTRY = 3

# ---------------------------------------------------------------- signal
Z_MA_WINDOW = 20
Z_EWMA_ALPHA = 0.06           # ~11 trading-day half-life
ENTRY_Z = 2.0
EXIT_Z = 0.0
STOP_Z = None                 # no hard stop
MAX_HOLD = None               # no time stop
COOLDOWN_DAYS = 5

# ---------------------------------------------------------------- regime
REGIME_WINDOW = 60            # days used to estimate market volatility
REGIME_WARMUP = 6             # rebalances needed before the percentile is used
REGIME_SIZING_MIN = 0.0       # s = REGIME_SIZING_SLOPE * p, clipped to [min, max]
REGIME_SIZING_MAX = 2.0
REGIME_TP_SLOPE = 1.0         # exit threshold scales with the regime percentile
MULT_WINDOW = 504             # trailing days used for the position-count p95
MULT_MIN_HISTORY = 250
MULT_QUANTILE = 0.95
MULT_MIN = 1.0
MULT_MAX = 3.0
GROSS_CAP = 1.0               # total notional / equity never exceeds 100%

# ---------------------------------------------------------------- costs
COST_PER_SIDE = 0.0005        # 5bp
BORROW_RATE = 0.04            # annualised, short leg
FUNDING_RATE = 0.02           # annualised, long leg
IDLE_YIELD = 0.02             # annualised, cash / reverse repo
