"""候選股票池：上市普通股中，as_of 時已公布股本最大的前 N 檔。

point-in-time：每個 as_of 用當時已公布的股本排名，回測時每年各自建池，
避免用「今天的大型股」回頭選股。限制：FinMind 股票清單只含目前仍掛牌的公司，
已下市股票無法納入（輕度存活者偏差，報告會註明）。
"""
from __future__ import annotations

import re

import pandas as pd

from .fetch import fetch
from .fundamentals import share_capital_at

EXCLUDED_INDUSTRIES = {"ETF", "ETN", "存託憑證", "所有證券", "創新板股票", "Index", "大盤"}


def candidate_universe(market: str = "twse") -> pd.DataFrame:
    """上市（twse）普通股：4 碼、非 0 開頭（排除 ETF/權證）、排除 ETF/存託憑證等類別。
    同一檔可能因多重產業分類出現多列 → 依 stock_id 去重。"""
    info = fetch("TaiwanStockInfo", "", "1990-01-01")
    if info.empty:
        return pd.DataFrame(columns=["stock_id", "stock_name", "industry"])
    df = info.copy()
    df["stock_id"] = df["stock_id"].astype(str)
    df = df[df["type"] == market]
    df = df[df["stock_id"].map(lambda s: bool(re.fullmatch(r"[1-9]\d{3}", s)))]
    bad = set(df.loc[df["industry_category"].isin(EXCLUDED_INDUSTRIES), "stock_id"])
    df = df[~df["stock_id"].isin(bad)]
    df = df.rename(columns={"industry_category": "industry"})
    return (df.groupby("stock_id", as_index=False)
              .agg(stock_name=("stock_name", "first"),
                   industry=("industry", lambda x: "/".join(sorted(set(x)))))
              .reset_index(drop=True))


def build_pool(as_of, size: int = 150, universe: pd.DataFrame | None = None,
               log=print) -> pd.DataFrame:
    """回 stock_id, stock_name, industry, share_capital(元)，依股本由大到小取前 size 檔。
    第一次跑要逐檔抓資產負債表（上市約 1000 檔，免費版約 2 小時）；之後走快取。"""
    uni = candidate_universe() if universe is None else universe
    rows = []
    n = len(uni)
    for i, r in enumerate(uni.itertuples(index=False), 1):
        if i % 50 == 0 or i == n:
            log(f"  股本掃描 {i}/{n}")
        try:
            cap = share_capital_at(r.stock_id, as_of)
        except Exception as e:                 # 單檔壞資料不影響整池
            log(f"  ⚠️ {r.stock_id} 股本讀取失敗：{str(e)[:60]}")
            cap = None
        if cap:
            rows.append({"stock_id": r.stock_id, "stock_name": r.stock_name,
                         "industry": r.industry, "share_capital": cap})
    pool = pd.DataFrame(rows, columns=["stock_id", "stock_name", "industry", "share_capital"])
    return pool.sort_values("share_capital", ascending=False).head(size).reset_index(drop=True)
