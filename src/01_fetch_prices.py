"""用腾讯行情接口抓取 A 股全历史日线（前复权 + 成交量），支持向前翻页。

腾讯接口单次最多 640 根，所以从最新日期往前翻，直到 2010 年。
产物：data/tencent_px.parquet（收盘价）、data/tencent_vol.parquet（成交量）
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from config import DATA
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
END = "2026-11-30"
FLOOR = "2010-01-01"


def sym(code):
    return ("sh" if code[0] in "69" else "sz") + code


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=HDR)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def fetch(code, adj="qfq"):
    s = sym(code)
    end, px, vol = END, {}, {}
    for _ in range(9):
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
               f"param={s},day,{FLOOR},{end},640,{adj}")
        try:
            j = get(url)
        except Exception:
            time.sleep(0.6)
            try:
                j = get(url)
            except Exception:
                break
        node = (j.get("data") or {})
        node = node.get(s) if isinstance(node, dict) else None
        if not node:
            break
        k = node.get("qfqday") or node.get("day") or []
        if not k:
            break
        for r in k:
            px[r[0]] = float(r[2])
            vol[r[0]] = float(r[5])
        first = k[0][0]
        if first <= FLOOR:
            break
        end = (pd.Timestamp(first) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if len(px) < 50:
        return code, None, None
    idx = pd.to_datetime(list(px.keys()))
    return (code, pd.Series(list(px.values()), index=idx).sort_index(),
            pd.Series(list(vol.values()), index=idx).sort_index())


def main(topn=1600, adj="qfq"):
    snap = pd.read_parquet(os.path.join(DATA, "snapshot.parquet")).set_index("code")
    snap = snap[snap["mcap"].notna()].sort_values("mcap", ascending=False)
    codes = [c for c in snap.index if len(c) == 6 and c[0] in "036"][:topn]
    print("target codes", len(codes), "adj=", adj or "raw", flush=True)
    t0, done, P, V = time.time(), 0, {}, {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, p, v in ex.map(lambda c: fetch(c, adj), codes):
            done += 1
            if p is not None:
                P[code] = p
                V[code] = v
            if done % 500 == 0:
                print(f"  {done}/{len(codes)} ok={len(P)} {time.time()-t0:.0f}s", flush=True)
    px = pd.DataFrame(P).sort_index()
    vol = pd.DataFrame(V).sort_index()
    tag = "raw" if adj == "" else adj
    px.to_parquet(os.path.join(DATA, f"tencent_px_{tag}.parquet"))
    vol.to_parquet(os.path.join(DATA, f"tencent_vol_{tag}.parquet"))
    print(f"px_{tag}", px.shape, px.index.min().date(), "->", px.index.max().date(),
          f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
