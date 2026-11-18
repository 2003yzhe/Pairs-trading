"""v3 候选池：不再用 ADF 直接筛，而是把**所有**评价指标都算出来存着，
让上层用不同的筛选组合去选。独立新文件，不动任何旧代码。

对每个配对计算（形成期 504 日，停牌不填充：要求两只股票在 504 日内至少 495 天有真实成交价）
  EG 预筛 p          —— p<=0.20 才进入后续（唯一的前置门槛，仅为控制计算量）
  johansen 通过       —— det_order=0, k_ar_diff=1, 5%
  beta_full           —— Johansen 第一特征向量（全形成期）
  adf_full            —— 用 beta_full 的价差在全形成期上的 ADF p
  beta_tr             —— 前 378 日 OLS 斜率（两段式训练段）
  adf_test            —— 用 beta_tr 在**后 126 日**上的 ADF p（真·样本外）
  stab                —— 训练段前后半均值差 |mu1-mu2|/sd
  t_trend             —— 全形成期价差对时间回归的 t 值
  nzc                 —— 全形成期价差穿越自身均值的次数
  hl                  —— 半衰期
  mu, sd              —— 全形成期均值/标准差（静态 z 用）

产物 pools_v3_semi.pkl
"""
import importlib.util
import os
import time

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen

from config import DATA
from config import OUT
def half_life(spread):
    """AR(1) half-life in trading days."""
    s = spread.dropna()
    if len(s) < 30:
        return float("nan")
    lag = s.shift(1).dropna()
    dy = s.diff().dropna()
    ix = lag.index.intersection(dy.index)
    if len(ix) < 30:
        return float("nan")
    x = lag.loc[ix].values
    y = dy.loc[ix].values
    k = float(np.linalg.lstsq(np.column_stack([np.ones(len(x)), x]), y, rcond=None)[0][1])
    if k >= 0:
        return float("inf")
    return float(-np.log(2) / np.log(1 + k))

snap = pd.read_parquet(os.path.join(DATA, "snapshot.parquet")).set_index("code")
adj = pd.read_parquet(os.path.join(DATA, "tencent_px_qfq.parquet")).sort_index()  # 全市场，不 ffill
sw = pd.read_csv(os.path.join(DATA, "sw_map.csv"), dtype={"code": str})
sw["code"] = sw["code"].str.zfill(6)
L2 = dict(zip(sw["code"], sw["sw_l2"]))
codes0 = [c for c in adj.columns if c in L2]
ind = pd.Series({c: L2.get(c, "-") for c in codes0}).astype(str)
vc = ind.value_counts()
keep = vc[vc >= 6].index
codes = [c for c in codes0 if ind[c] in keep]
print("全市场可用", len(codes0), "落到合格行业", len(codes), flush=True)

# 每期 PIT 大市值池
pit = pd.read_csv(os.path.join(DATA, "pit_universe.csv"))
pit["date"] = pd.to_datetime(pit["date"])
PIT = {T: set(g["code"].astype(str).str.zfill(6)) for T, g in pit.groupby("date")}

# 市值代理（60 日均值，只用于配对的规模匹配）
px_last = adj.ffill().iloc[-1]
shares = (snap["mcap"] / px_last).reindex(codes)
mcap_proxy = pd.DataFrame({c: shares[c] * adj[c] for c in codes if c in adj.columns}).ffill()

days = adj.index
QENDS = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
grid = sorted({days[days <= q][-1] for q in QENDS if len(days[days <= q])})
grid = [T for T in grid if len(days[days <= T]) >= 504 and T.month in (6, 12)]
print("rebalances", len(grid), flush=True)

TRAIN, TEST = 378, 126


def metrics(pa, pb):
    """pa/pb: 形成期 ln 价格（已对齐）"""
    n = len(pa)
    if n < 495:
        return None
    try:
        jr = coint_johansen(np.column_stack([pa.values, pb.values]), det_order=0, k_ar_diff=1)
        if not (jr.lr1[0] > jr.cvt[0, 1]):
            return None
        ev = jr.evec[:, 0]
        beta = float(-ev[1] / ev[0]) if abs(ev[0]) > 1e-8 else np.nan
    except Exception:
        return None
    if not (np.isfinite(beta) and beta > 0):
        return None
    spr = pa - beta * pb
    try:
        adf_full = float(adfuller(spr.values, maxlag=5, regression="c", autolag="AIC")[1])
    except Exception:
        adf_full = np.nan
    mu, sd = float(spr.mean()), float(spr.std())
    if not (np.isfinite(sd) and sd > 0):
        return None
    h = n // 2
    mu1, mu2 = float(spr.iloc[:h].mean()), float(spr.iloc[h:].mean())
    stab = abs(mu1 - mu2) / sd
    x = np.arange(n)
    A = np.column_stack([np.ones(n), x])
    coef, *_ = np.linalg.lstsq(A, spr.values, rcond=None)
    resid = spr.values - A @ coef
    s2 = float(resid @ resid) / max(n - 2, 1)
    se = np.sqrt(s2 * np.linalg.inv(A.T @ A)[1, 1])
    t_trend = float(coef[1] / se) if se > 0 else np.nan
    sign = np.sign(spr.values - mu)
    nzc = int((sign[1:] * sign[:-1] < 0).sum())
    hl = half_life(spr)
    # ---- 两段式：前 TRAIN 日估 beta，后 TEST 日验平稳
    pa_tr, pb_tr = pa.iloc[:TRAIN], pb.iloc[:TRAIN]
    pa_te, pb_te = pa.iloc[-TEST:], pb.iloc[-TEST:]
    try:
        b_tr = float(np.linalg.lstsq(np.column_stack([np.ones(len(pb_tr)), pb_tr.values]),
                                     pa_tr.values, rcond=None)[0][1])
    except Exception:
        return None
    if not (np.isfinite(b_tr) and b_tr > 0):
        return None
    spr_te = pa_te - b_tr * pb_te
    try:
        adf_test = float(adfuller(spr_te.values, maxlag=5, regression="c", autolag="AIC")[1])
    except Exception:
        adf_test = np.nan
    return dict(beta=beta, adf_full=adf_full, mu=mu, sd=sd, stab=stab,
                t_trend=t_trend, nzc=nzc, hl=hl, beta_tr=b_tr, adf_test=adf_test)


out = []
for T in grid:
    t0 = time.time()
    members = [c for c in codes if c in PIT.get(T, set())]
    hist = adj[members].loc[:T].tail(504).dropna(axis=1, thresh=int(504 * 0.95))
    mc = mcap_proxy.loc[:T].iloc[-1]
    cands, n_eg = [], 0
    for g in keep:
        names = [c for c in hist.columns if ind[c] == g]
        if len(names) < 6:
            continue
        lp = {c: np.log(hist[c].dropna()) for c in names}
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                pa, pb = lp[a], lp[b]
                ix = pa.index.intersection(pb.index)
                if len(ix) < 495:
                    continue
                pa, pb = pa.loc[ix], pb.loc[ix]
                try:
                    mi, mj = float(mc[a]), float(mc[b])
                except Exception:
                    continue
                if not (np.isfinite(mi) and np.isfinite(mj)) or mi <= 0 or mj <= 0:
                    continue
                if abs(np.log(mi / mj)) > 1.0:
                    continue
                try:
                    b_ols = float(np.linalg.lstsq(
                        np.column_stack([np.ones(len(pb)), pb.values]), pa.values,
                        rcond=None)[0][1])
                except Exception:
                    continue
                if not (np.isfinite(b_ols) and b_ols > 0):
                    continue
                try:
                    p_eg = adfuller((pa - b_ols * pb).values, maxlag=5,
                                    regression="c", autolag="AIC")[1]
                except Exception:
                    continue
                if p_eg > 0.20:
                    continue
                n_eg += 1
                mt = metrics(pa, pb)
                if mt is None:
                    continue
                cands.append(dict(industry=g, a=a, b=b, eg_p=float(p_eg), **mt))
    out.append((T, cands))
    print(f"  {T.date()} EG {n_eg} -> 通过johansen {len(cands)}  ({time.time()-t0:.1f}s)",
          flush=True)

pd.to_pickle(out, os.path.join(DATA, "pools_v3_pit.pkl"))
print("saved pools_v3_pit.pkl")
