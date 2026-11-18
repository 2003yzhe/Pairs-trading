"""构建 Point-in-Time 大市值股票池（不依赖指数公司的历史成分）。

用腾讯的全市场行情：每个调仓日按"过去 60 日平均成交额"重排，取前 800 只。
  成交额代理 = 前复权收盘价 × 成交量
  （前复权对每只股票只差一个时间不变的常数，对横截面排名影响有限；
    高分红股的历史成交额会被低估，这是已知近似）

筛选：形成期内 ≥495/504 天有真实成交价（停牌不填充）、名称不含 ST、有申万行业。
校验：2025-12 的 PIT top800 与今天真实中证800成分的重叠率。
"""
import os

import numpy as np
import pandas as pd

from config import DATA
END = pd.Timestamp("2026-11-18")

px = pd.read_parquet(os.path.join(DATA, "tencent_px_qfq.parquet")).sort_index()
vol = pd.read_parquet(os.path.join(DATA, "tencent_vol_qfq.parquet")).sort_index()
px = px.loc[px.index <= END]
vol = vol.reindex(px.index)

snap = pd.read_parquet(os.path.join(DATA, "snapshot.parquet")).set_index("code")
name = snap["name"].astype(str)
sw = pd.read_csv(os.path.join(DATA, "sw_map.csv"), dtype={"code": str})
sw["code"] = sw["code"].str.zfill(6)
L2 = dict(zip(sw["code"], sw["sw_l2"]))

codes = [c for c in px.columns if c in vol.columns and c in L2]
codes = [c for c in codes if "ST" not in name.get(c, "") and "退" not in name.get(c, "")]
print("行情股票", px.shape[1], "→ 有行业且非ST", len(codes))

turn = (px[codes] * vol[codes]).replace([np.inf, -np.inf], np.nan)
print("成交额矩阵", turn.shape)

days = px.index
QENDS = pd.date_range("2012-03-31", "2026-12-31", freq="QE")
grid = sorted({days[days <= q][-1] for q in QENDS if len(days[days <= q])})
grid = [T for T in grid if len(days[days <= T]) >= 504 and T.month in (6, 12)]
print("调仓日", len(grid))

rows = []
last_px = px.ffill().iloc[-1]
mcap_now = snap["mcap"].reindex(codes)
shares_now = mcap_now / last_px          # 当前总股本（前复权已在最新日锚定真实价）
for T in grid:
    win_px = px[codes].loc[:T].tail(504)
    ok = [c for c in codes if int(win_px[c].isna().sum()) <= 9]
    if len(ok) < 200:
        continue
    mc = shares_now[ok] * px[ok].loc[:T].tail(60).mean()   # 60 日平均市值代理
    mc = mc.replace([np.inf, -np.inf], np.nan).dropna()
    mc = mc[mc > 0]
    if len(mc) < 200:
        continue
    rank = mc.rank(ascending=False)
    top = rank[rank <= 800].index
    for c in top:
        rows.append(dict(date=T, code=c, rank=int(rank[c])))
    print(f"  {T.date()}: 干净 {len(ok):4d}  有市值 {len(mc):4d}  top800 {len(top):4d}",
          flush=True)

pit = pd.DataFrame(rows)
pit.to_csv(os.path.join(DATA, "pit_universe.csv"), index=False)
print("\n每期 top800：", pit.groupby("date").size().describe().round(1).to_dict())

# ---- 校验：与今天真实中证800 的重叠
real = set(pd.read_csv(os.path.join(DATA, "universe.csv"),
                       dtype={"code": str})["code"].str.zfill(6))
print("\n与今天真实中证800成分的重叠：")
for T in [grid[0], grid[len(grid) // 3], grid[2 * len(grid) // 3], grid[-1]]:
    cur = set(pit[pit["date"] == T]["code"])
    print(f"  {T.date()}: PIT {len(cur)} 只，重叠 {len(cur & real)} 只 "
          f"({len(cur & real)/max(len(cur),1)*100:.0f}%)")
