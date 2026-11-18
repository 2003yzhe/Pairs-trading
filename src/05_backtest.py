"""End-to-end backtest.

Usage:
    python src/05_backtest.py

Reads  data/pools_v3_pit.pkl   (produced by 04_build_pairs.py)
       data/index_long.parquet (produced by 02_fetch_index.py)
Writes results/*.csv and prints a summary table.

Everything below is computed from data available strictly before each
rebalance date; no future information enters the signal, the gates, the
market-volatility regime, or the position-size multiplier.
"""
import json
import os

import numpy as np
import pandas as pd

import engine as E
from config import (ADF_FULL_MAX, ADF_TEST_MAX, BORROW_RATE, COST_PER_SIDE,
                    DATA, ENTRY_Z, EXIT_Z, FUNDING_RATE, GROSS_CAP, IDLE_YIELD,
                    MAX_PER_INDUSTRY, MULT_MAX, MULT_MIN, MULT_MIN_HISTORY,
                    MULT_QUANTILE, MULT_WINDOW, OUT, REGIME_SIZING_MAX,
                    REGIME_SIZING_MIN, REGIME_TP_SLOPE, REGIME_WARMUP,
                    REGIME_WINDOW, TARGET_PAIRS)

idx = pd.read_parquet(os.path.join(DATA, "index_long.parquet")).sort_index()
hs_log = np.log(idx["hs300"]).diff()

pools = pd.read_pickle(os.path.join(DATA, "pools_v3_pit.pkl"))

# ---------------------------------------------------------------- candidates
SCR = []
for T, cs in pools:
    d = pd.DataFrame(cs)
    if len(d) == 0:
        SCR.append((T, []))
        continue
    d = d[(d.adf_full <= ADF_FULL_MAX) & (d.adf_test <= ADF_TEST_MAX)].copy()
    if len(d) == 0:
        SCR.append((T, []))
        continue
    d["adf_p"] = d["stab"].values          # rank by mean stability
    SCR.append((T, d.to_dict("records")))

# ---------------------------------------------------------------- regime
mvol_series = hs_log.rolling(REGIME_WINDOW).std() * np.sqrt(252) * 100
PCT, hist = {}, []
for T, _ in SCR:
    v = float(mvol_series.loc[:T].dropna().iloc[-1]) if len(mvol_series.loc[:T].dropna()) else np.nan
    if np.isfinite(v) and len(hist) >= REGIME_WARMUP:
        PCT[T] = float(np.mean([h < v for h in hist]))
    else:
        PCT[T] = 0.5
    hist.append(v)

SIZE_MAP = {T: float(np.clip(REGIME_SIZING_MIN + REGIME_SIZING_MAX * p,
                             REGIME_SIZING_MIN, REGIME_SIZING_MAX))
            for T, p in PCT.items()}
TP_MAP = {T: REGIME_TP_SLOPE * p for T, p in PCT.items()}

BASE = dict(carry=True, z_mode="dyn", entry=ENTRY_Z, tp=EXIT_Z, stop=None,
            maxhold="none", target_n=TARGET_PAIRS, max_per_ind=MAX_PER_INDUSTRY,
            cost=COST_PER_SIDE, borrow=BORROW_RATE, fund_rate=FUNDING_RATE,
            gross_cap=GROSS_CAP, size_map=SIZE_MAP, tp_map=TP_MAP,
            idle_yield=IDLE_YIELD)

# ---------------------------------------------------------------- multiplier
c1, _ = E.run(SCR, **BASE, lev=1.0)
npos = pd.Series(E.STATS["npos"], index=c1["equity"].index)

LMAP = {}
for T, _ in SCR:
    h = npos.loc[:T].iloc[:-1]
    if len(h) < MULT_MIN_HISTORY:
        LMAP[T] = 1.0
        continue
    p = float(np.quantile(h.tail(MULT_WINDOW), MULT_QUANTILE))
    LMAP[T] = float(np.clip(TARGET_PAIRS / max(p, 2.0), MULT_MIN, MULT_MAX))

SIZE2 = {T: SIZE_MAP[T] * LMAP[T] for T in SIZE_MAP}


def stats(c):
    s = c["equity"].dropna()
    s = s / s.iloc[0]
    r = s.pct_change().dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    j = np.log(s).diff().dropna().index.intersection(hs_log.index)
    y, x = np.log(s).diff().dropna().loc[j].values, hs_log.loc[j].values
    X = np.column_stack([np.ones(len(x)), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    s2 = float(e @ e) / (len(y) - 2)
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    return dict(total=(s.iloc[-1] - 1) * 100,
                cagr=((s.iloc[-1]) ** (1 / yrs) - 1) * 100,
                vol=float(r.std() * np.sqrt(252) * 100),
                sharpe=float(r.mean() / r.std() * np.sqrt(252)),
                mdd=float((s / s.cummax() - 1).min()) * 100,
                alpha=float(b[0]) * 252 * 100, t=float(b[0] / se[0]),
                beta=float(b[1]))


rows, curves = [], {}
for lab, sm in (("dynamic_multiplier", SIZE2),
                ("fixed_multiplier_1.0", SIZE_MAP)):
    c, tr = E.run(SCR, **{**BASE, "size_map": sm})
    st = stats(c)
    rows.append(dict(config=lab, **st, trades=len(tr),
                     avg_gross=float(np.mean(E.STATS["gross"])),
                     max_gross=float(np.max(E.STATS["gross"]))))
    curves[lab] = c["equity"].dropna()
    print(f"{lab:22s} cagr {st['cagr']:6.2f}%  vol {st['vol']:5.2f}%  "
          f"sharpe {st['sharpe']:5.2f}  mdd {st['mdd']:6.2f}%  "
          f"alpha {st['alpha']:5.2f}% (t={st['t']:4.2f})  beta {st['beta']:+.3f}")

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT, "summary.csv"), index=False)
pd.DataFrame(curves).to_csv(os.path.join(OUT, "equity_curves.csv"))
json.dump({str(k): v for k, v in LMAP.items()},
          open(os.path.join(OUT, "multiplier_path.json"), "w"), indent=1)
print("\nsaved results/summary.csv")
