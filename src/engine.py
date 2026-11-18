"""engine_v3：支持"连续持仓"的新引擎（独立文件，不动旧代码）。

与旧引擎的唯一结构差别：
  carry=False  每期期末强制平仓（= 旧引擎行为，用于验证等价性）
  carry=True   持仓跨期；只平"不在新一期名单里"的仓（状态机 T12）；
               期内任何一天都可开新仓，只要该对在当前名单里。

cap 约定：carry=False 用"期初权益/当期权对数"（与旧引擎一致）；
          carry=True  用"开仓时权益/target_n"（槽位法）。
其余一致：等权、5bp/边、融券 5%/年、涨跌停拦单、冷却 5 日、z 口径可选。
"""
import os

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from config import DATA
END = pd.Timestamp("2026-11-18")          # 长样本回测的截止日
snap = pd.read_parquet(os.path.join(DATA, "snapshot.parquet")).set_index("code")
raw = pd.read_parquet(os.path.join(DATA, "tencent_px_qfq.parquet")).sort_index()
raw = raw.loc[raw.index <= END]
px = raw.ffill()
codes0 = list(raw.columns)
name_all = snap["name"].astype(str)
tdays = list(raw.index)
tloc = {d: i for i, d in enumerate(tdays)}

COST, BORROW = 0.0005, 0.05
STATS = {"blocked": 0, "truncated": 0, "max_gross": 0.0, "gross": [], "npos": []}


def _lim(c):
    if c.startswith("688") or c.startswith("30"):
        return 0.20
    return 0.05 if "ST" in name_all.get(c, "") else 0.10


LIMIT = {c: _lim(c) for c in codes0}


def at_limit(code, d):
    i = tloc[d]
    if i == 0:
        return 0
    c0, c1 = raw[code].iloc[i - 1], raw[code].iloc[i]
    if not (np.isfinite(c0) and np.isfinite(c1)) or c0 <= 0:
        return 2
    pct = c1 / c0 - 1
    lim = LIMIT.get(code, 0.10)
    if pct >= lim - 0.003:
        return 1
    if pct <= -lim + 0.003:
        return -1
    return 0


def _z(series, mode, mu, sd):
    """mode: "dyn" | "static" | "roll<N>"（N 为交易日数，滚动均值+滚动标准差）"""
    if mode == "dyn":
        dev = series - series.rolling(20).mean()
        var = dev.pow(2).ewm(alpha=0.06, adjust=False).mean()
        return dev / np.sqrt(var)
    if mode == "static":
        return (series - mu) / sd
    if mode.startswith("roll"):
        n = int(mode[4:])
        m = series.rolling(n).mean()
        s = series.rolling(n).std()
        return (series - m) / s
    raise ValueError(mode)


def select(cands, n, max_per_ind):
    out, used, ic = [], set(), {}
    for c in sorted(cands, key=lambda x: x["adf_p"]):
        if len(out) >= n:
            break
        if c["a"] in used or c["b"] in used:
            continue
        if ic.get(c["industry"], 0) >= max_per_ind:
            continue
        out.append(c)
        used.add(c["a"])
        used.add(c["b"])
        ic[c["industry"]] = ic.get(c["industry"], 0) + 1
    return out


def run(pools, carry=False, z_mode="dyn", entry=2.0, tp=0.3, stop=3.5, maxhold="3hl",
        recheck=False, target_n=20, max_per_ind=7, cost=COST, borrow=BORROW,
        fund_rate=0.0, lev=1.0, exit_ref="dyn", size_map=None,
        entry_map=None, tp_map=None, gross_cap=None,
        idle_yield=0.0, idle_px=None, cap_mode="fixed"):
    """fund_rate: 多头腿的年化融资利率（两融/收益互换的资金成本）
       lev      : 名义杠杆倍数（cap 乘以 lev，gross 敞口随之放大）
       exit_ref : "dyn"   出场用当期 z（均值跟着 MA20 走）
                  "entry" 出场用进场时的均值 mu0（固定参照系，只让 σ 自适应）
       size_map : {调仓日: 仓位乘数}，按 regime 连续调节仓位（默认 1.0）
       entry_map: {调仓日: 入场阈值}，按 regime 调节 z 阈值（默认用 entry）
       tp_map   : {调仓日: 出场阈值}，按 regime 调节 z 阈值（默认用 tp）
       gross_cap: 总敞口上限（占权益比例）。None=不限；1.0=总名义敞口不超过 100% 权益
       idle_yield: 闲置资金的无风险年化收益（如逆回购/货基）
       idle_px   : 闲置资金投向的指数价格序列（如沪深300），与 idle_yield 二选一
       cap_mode  : "fixed" = 权益/target_n（固定槽位）
                   "sel"   = 权益/len(sel)（按实际选出的对数分，选不满时每对更大）"""
    STATS["blocked"] = STATS["truncated"] = 0
    STATS["max_gross"] = 0.0
    STATS["gross"] = []
    STATS["npos"] = []
    size_map = size_map or {}
    entry_map = entry_map or {}
    tp_map = tp_map or {}
    equity, curve, trades = 1.0, [], []
    book, cool = {}, {}
    dates = [T for T, _ in pools]

    for k, (T, cands) in enumerate(pools):
        qend = dates[k + 1] if k + 1 < len(dates) else tdays[-1]
        days = [d for d in tdays if d > T and d <= qend]
        if len(days) < 5:
            continue
        sel = select(cands, target_n, max_per_ind)
        idx = [T] + days
        zmap, smap, cmap = {}, {}, {}
        for c in sel:
            key = (c["a"], c["b"])
            s_full = np.log(px[c["a"]]) - c["beta"] * np.log(px[c["b"]])
            zmap[key] = _z(s_full, z_mode, c.get("mu"), c.get("sd")).reindex(idx).values
            smap[key] = s_full.reindex(idx).values
            cmap[key] = c
        selkeys = set(zmap)
        nslot = len(sel) if (cap_mode == "sel" and sel) else target_n
        cap_period = (equity / nslot * lev * size_map.get(T, 1.0)) if sel else 0.0
        pending = [k2 for k2 in book if k2 not in selkeys] if carry else []

        for j, d in enumerate(days):
            day_pnl = 0.0
            prev = days[j - 1] if j > 0 else T
            if carry and j == 0:
                for key in pending:
                    if key in book:
                        cap = book[key]["cap"]
                        book[key]["pnl"] -= cost * cap
                        day_pnl -= cost * cap
                        trades.append(dict(pnl=book[key]["pnl"],
                                           pnl_pct=book[key]["pnl"] / cap,
                                           days=book[key]["held"], reason="out",
                                           industry=book[key]["industry"]))
                        cool[key] = 5
                        del book[key]
            # ---- 持仓
            for key in list(book):
                p = book[key]
                beta, cap = p["beta"], p["cap"]
                scale = cap / (1 + beta)
                dsp = p["s_all"][tloc[d]] - p["s_all"][tloc[prev]]
                if np.isfinite(dsp):
                    gain = p["dir"] * dsp * scale
                    p["pnl"] += gain
                    day_pnl += gain
                sn = (beta if p["dir"] == 1 else 1.0) / (1 + beta) * cap
                bc = borrow / 252 * sn
                ln_leg = (1.0 if p["dir"] == 1 else beta) / (1 + beta) * cap
                fc = fund_rate / 252 * ln_leg
                p["pnl"] -= bc + fc
                day_pnl -= bc + fc
                p["held"] += 1
                if exit_ref == "entry":
                    sv = p["sig_all"][tloc[prev]]
                    zp = ((p["s_all"][tloc[prev]] - p["mu0"]) / sv
                          if np.isfinite(sv) and sv > 0 else np.nan)
                else:
                    zp = p["z_all"][tloc[prev]]
                hit_stop = stop is not None and np.isfinite(zp) and (
                    (p["dir"] == 1 and zp <= -stop) or (p["dir"] == -1 and zp >= stop))
                tp_t = tp_map.get(p["pt"], tp)
                hit_tp = tp_t is not None and np.isfinite(zp) and (
                    (p["dir"] == 1 and zp >= tp_t) or (p["dir"] == -1 and zp <= -tp_t))
                hit_time = p["mh"] is not None and p["held"] >= p["mh"]
                hit_brk = False
                if recheck and not (hit_stop or hit_tp or hit_time) and p["held"] % 20 == 0:
                    seg = px[p["a"]].loc[:d].tail(252)
                    segb = px[p["b"]].loc[:d].tail(252)
                    spr = (np.log(seg) - beta * np.log(segb)).dropna()
                    if len(spr) > 60:
                        try:
                            hit_brk = adfuller(spr.values, maxlag=5, regression="c",
                                               autolag="AIC")[1] > 0.10
                        except Exception:
                            hit_brk = False
                if hit_stop or hit_tp or hit_time or hit_brk:
                    sell_leg, buy_leg = ((p["a"], p["b"]) if p["dir"] == 1
                                         else (p["b"], p["a"]))
                    if at_limit(sell_leg, d) not in (-1, 2) and \
                            at_limit(buy_leg, d) not in (1, 2):
                        p["pnl"] -= cost * cap
                        day_pnl -= cost * cap
                        trades.append(dict(pnl=p["pnl"], pnl_pct=p["pnl"] / cap,
                                           days=p["held"], industry=p["industry"],
                                           reason=("sl" if hit_stop else "tp" if hit_tp
                                                   else "brk" if hit_brk else "time")))
                        if hit_stop or hit_time or hit_brk:
                            cool[key] = 5
                        del book[key]
            # ---- 开仓
            for key in selkeys:
                if key in book:
                    continue
                if cool.get(key, 0) > 0:
                    cool[key] -= 1
                    continue
                zp = zmap[key][j]
                if not (np.isfinite(zp) and abs(zp) >= entry_map.get(T, entry)):
                    continue
                if len(book) >= target_n:
                    break
                go = -1 if zp > 0 else 1
                a, b = key
                buy_leg, sell_leg = (a, b) if go == 1 else (b, a)
                if at_limit(buy_leg, d) in (1, 2) or at_limit(sell_leg, d) in (-1, 2):
                    continue
                c = cmap[key]
                beta = c["beta"]
                cap = (cap_period if not carry
                       else equity / nslot * lev * size_map.get(T, 1.0))
                if gross_cap is not None:
                    used = sum(q["cap"] for q in book.values())
                    room = gross_cap * equity - used
                    cap = min(cap, room)
                if cap <= 1e-9:
                    continue
                mh = None if maxhold == "none" else (
                    min(40, max(10, int(3 * c["hl"]))) if maxhold == "3hl" else int(maxhold))
                spr = np.log(px[a]) - beta * np.log(px[b])
                dev = spr - spr.rolling(20).mean()
                sig_all = np.sqrt(dev.pow(2).ewm(alpha=0.06, adjust=False).mean())
                mu0 = (spr.rolling(20).mean().loc[d] if z_mode == "dyn"
                       else float(c.get("mu", spr.mean())))
                book[key] = dict(a=a, b=b, beta=beta, dir=go, held=0, pnl=-cost * cap,
                                 cap=cap, mh=mh, industry=c["industry"], pt=T,
                                 mu0=mu0, sig_all=sig_all.values,
                                 s_all=spr.values, z_all=_z(spr, z_mode, c.get("mu"),
                                                            c.get("sd")).values)
                day_pnl -= cost * cap
            # ---- 闲置资金的收益
            if idle_yield or idle_px is not None:
                busy = sum(q["cap"] for q in book.values())
                idle = equity - busy
                if idle > 0:
                    if idle_px is not None:
                        i = tloc[d]
                        if i > 0:
                            r_ = idle_px.get(d, np.nan)
                            r0 = idle_px.get(tdays[i - 1], np.nan)
                            if np.isfinite(r_) and np.isfinite(r0) and r0 > 0:
                                day_pnl += idle * (r_ / r0 - 1)
                    else:
                        day_pnl += idle * idle_yield / 252
            equity += day_pnl
            g = sum(q["cap"] for q in book.values()) / max(equity, 1e-9)
            STATS["gross"].append(g)
            STATS["npos"].append(len(book))
            STATS["max_gross"] = max(STATS["max_gross"], g)
            curve.append((d, equity))
        if not carry:
            ec = 0.0
            for key in list(book):
                cap = book[key]["cap"]
                book[key]["pnl"] -= cost * cap
                trades.append(dict(pnl=book[key]["pnl"], pnl_pct=book[key]["pnl"] / cap,
                                   days=book[key]["held"], reason="eop",
                                   industry=book[key]["industry"]))
                ec -= cost * cap
                del book[key]
            equity += ec
            if curve:
                curve[-1] = (curve[-1][0], equity)
    for key in list(book):
        cap = book[key]["cap"]
        book[key]["pnl"] -= cost * cap
        trades.append(dict(pnl=book[key]["pnl"], pnl_pct=book[key]["pnl"] / cap,
                           days=book[key]["held"], reason="eop",
                           industry=book[key]["industry"]))
        equity += book[key]["pnl"] + 0.0
        del book[key]
    cdf = pd.DataFrame(curve, columns=["date", "equity"]).set_index("date")
    return cdf, pd.DataFrame(trades)


def stats(c):
    r = c["equity"].pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return dict(total=round(float(c["equity"].iloc[-1] - 1) * 100, 2),
                cagr=round(float(c["equity"].iloc[-1] ** (1 / yrs) - 1) * 100, 2),
                vol=round(float(r.std() * np.sqrt(252)) * 100, 2),
                sharpe=round(float(r.mean() / r.std() * np.sqrt(252)), 2),
                maxdd=round(float((c["equity"] / c["equity"].cummax() - 1).min()) * 100, 2))
