"""抓取沪深300(000300)与中证500(000905)的日线，用于基线对比。"""
import json
import os
import time
import urllib.request

import pandas as pd

from config import DATA
HDR = {"User-Agent": "Mozilla/5.0"}
FLOOR, END = "2010-01-01", "2026-11-30"


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())


def fetch(sym):
    end, out = END, {}
    for _ in range(9):
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
               f"param={sym},day,{FLOOR},{end},640,")
        try:
            j = get(url)
        except Exception:
            time.sleep(0.8)
            continue
        node = (j.get("data") or {}).get(sym) or {}
        k = node.get("day") or node.get("qfqday") or []
        if not k:
            break
        for r in k:
            out[r[0]] = float(r[2])
        first = k[0][0]
        if first <= FLOOR:
            break
        end = (pd.Timestamp(first) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return pd.Series(out).sort_index()


frames = {}
for name, sym in (("hs300", "sh000300"), ("csi500", "sh000905")):
    s = fetch(sym)
    frames[name] = s
    print(name, len(s), s.index.min(), "->", s.index.max())
df = pd.DataFrame(frames)
df.index = pd.to_datetime(df.index)
df.to_parquet(os.path.join(DATA, "index_long.parquet"))
print(df.tail(3).to_string())
print("saved index_long.parquet", df.shape)
