"""Regenerate README figures from the backtest output.

Usage:
    python src/plot_results.py
"""
import os

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config import DATA, OUT

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False

curves = pd.read_csv(os.path.join(OUT, "equity_curves.csv"), index_col=0, parse_dates=True)
idx = pd.read_parquet(os.path.join(DATA, "index_long.parquet")).sort_index()

COL = "dynamic_multiplier"
s = curves[COL].dropna()
start = s.index[0]

b300 = idx["hs300"].loc[idx.index >= start]
b500 = idx["csi500"].loc[idx.index >= start]
b300 = b300 / b300.iloc[0]
b500 = b500 / b500.iloc[0]

r = s.pct_change().dropna()
yrs = (s.index[-1] - s.index[0]).days / 365.25
cagr = (s.iloc[-1] ** (1 / yrs) - 1) * 100
sharpe = float(r.mean() / r.std() * np.sqrt(252))
dd = (s / s.cummax() - 1) * 100

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12.4, 8.8), sharex=True,
                               gridspec_kw={"height_ratios": [2.3, 1.0]})

ax1.plot(s.index, s.values, lw=2.2, color="#B23A48",
         label=f"Strategy  CAGR {cagr:.2f}%  Sharpe {sharpe:.2f}  MaxDD {dd.min():.1f}%")
ax1.plot(b300.index, b300.values, lw=1.2, color="#E8A33D",
         label=f"CSI 300  {b300.iloc[-1] * 100 - 100:+.1f}%")
ax1.plot(b500.index, b500.values, lw=1.2, color="#7CA65B",
         label=f"CSI 500  {b500.iloc[-1] * 100 - 100:+.1f}%")
ax1.set_yscale("log")
ax1.set_ylabel("Net asset value (log scale)", fontsize=11)
ax1.set_title(
    "Industry-neutral pairs trading on CSI 800  |  Jul 2012 - Nov 2026\n"
    "Point-in-time universe  |  8 pairs, max 3 per SW level-2 industry  |  "
    "semi-annual rebalance  |  gross exposure <= 100%  |  unlevered",
    fontsize=11.5)
ax1.legend(fontsize=9.5, loc="upper left", framealpha=0.92)
ax1.grid(alpha=0.25, which="both")

ax2.fill_between(dd.index, dd.values, 0, color="#B23A48", alpha=0.22)
ax2.plot(dd.index, dd.values, lw=1.4, color="#B23A48",
         label=f"Strategy drawdown  {dd.min():.2f}%")
d300 = (b300 / b300.cummax() - 1) * 100
ax2.plot(d300.index, d300.values, lw=1.0, color="#E8A33D", alpha=0.85,
         label=f"CSI 300 drawdown  {d300.min():.1f}%")
ax2.axhline(0, lw=0.8, color="k", alpha=0.5)
ax2.set_ylabel("Drawdown (%)", fontsize=11)
ax2.legend(fontsize=9, loc="lower left", framealpha=0.92)
ax2.grid(alpha=0.25)

fig.tight_layout()
fig.savefig(os.path.join(OUT, "equity_curve.png"), dpi=170)
print("saved results/equity_curve.png")
