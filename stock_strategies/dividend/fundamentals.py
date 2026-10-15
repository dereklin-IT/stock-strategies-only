"""定存股篩選用的 point-in-time 基本資料。

所有函式都吃 as_of（決策日），只用「當天已經公開」的資料，避免回測偷看未來：
- 年報：會計年度 FY 的完整財報最晚 FY+1 年 3/31 公布 → latest_fy(as_of)
- 季度資產負債表（股本）：季末 + 45 天；第 4 季（併年報）+ 90 天
- 現金股利：以「除息交易日 ≤ as_of」的完整日曆年計
- 股價 / 本益比 / 股價淨值比：as_of 當天或之前最近一筆

欄位名稱依 tools/probe_finmind.py 實測（2412/2330/2886/2105）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data import EQUITY_TYPES, NET_INCOME_TYPES, adjust_for_dividends
from .fetch import fetch

SHARE_CAPITAL_TYPES = ["OrdinaryShare", "CapitalStock"]


def _ts(x) -> pd.Timestamp:
    return pd.Timestamp(x).normalize()


def latest_fy(as_of) -> int:
    """as_of 當天可取得完整年報的最新會計年度。"""
    as_of = _ts(as_of)
    return as_of.year - 1 if as_of >= pd.Timestamp(as_of.year, 3, 31) else as_of.year - 2


def _long(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not {"date", "type", "value"}.issubset(df.columns):
        return pd.DataFrame(columns=["date", "type", "value"])
    df = df[["date", "type", "value"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])


def _pick(df: pd.DataFrame, types: list[str]) -> pd.Series:
    """依優先序取第一個存在的科目，回 date→value。"""
    for t in types:
        sub = df[df["type"] == t]
        if not sub.empty:
            return sub.groupby("date")["value"].last().sort_index()
    return pd.Series(dtype=float)


def annual_fundamentals(stock_id: str) -> pd.DataFrame:
    """回 index=會計年度 的 DataFrame：
    eps, net_income, equity, roe(%，平均權益), debt_ratio(%), share_capital(元), bvps(元)。
    只收 4 季齊全的年度（eps / net_income）；資產負債以 12/31 那一筆為年底值。"""
    fs = _long(fetch("TaiwanStockFinancialStatements", stock_id))
    bs = _long(fetch("TaiwanStockBalanceSheet", stock_id))
    out = {}

    def annual_sum(series: pd.Series) -> pd.Series:
        if series.empty:
            return series
        g = series.groupby(series.index.year)
        s = g.sum()
        return s[g.count() >= 4]

    def year_end(series: pd.Series) -> pd.Series:
        if series.empty:
            return series
        ye = series[(series.index.month == 12) & (series.index.day == 31)]
        return ye.groupby(ye.index.year).last()

    out["eps"] = annual_sum(_pick(fs, ["EPS"]))
    out["net_income"] = annual_sum(_pick(fs, NET_INCOME_TYPES))
    out["equity"] = year_end(_pick(bs, EQUITY_TYPES))
    liab = year_end(_pick(bs, ["Liabilities"]))
    assets = year_end(_pick(bs, ["TotalAssets"]))
    out["debt_ratio"] = (liab / assets * 100) if not liab.empty and not assets.empty else pd.Series(dtype=float)
    out["share_capital"] = year_end(_pick(bs, SHARE_CAPITAL_TYPES))

    df = pd.DataFrame(out)
    if df.empty:
        return df
    df.index.name = "fy"
    df = df.sort_index()
    avg_eq = (df["equity"] + df["equity"].shift(1)) / 2
    avg_eq = avg_eq.fillna(df["equity"])          # 第一年沒有期初 → 用期末
    df["roe"] = df["net_income"] / avg_eq * 100
    df["bvps"] = df["equity"] / (df["share_capital"] / 10)   # 面額 10 元
    return df


MAX_FILING_AGE_DAYS = 270   # 最新財報季末距 as_of 超過此天數 → 視為已下市/合併（不在池內）


def share_capital_at(stock_id: str, as_of, max_age_days: int = MAX_FILING_AGE_DAYS) -> float | None:
    """as_of 時已公布的最新股本（元）。季報季末+45 天、年報(12/31)+90 天才算公開。

    FinMind 股票清單含已下市/合併公司（日月光 2311、矽品 2325、晶電、新光金…），
    它們最後一份財報停在多年前。若最新可用財報的季末距 as_of 超過 max_age_days，
    回 None（當時已不交易）。正常情況最新季末距今最多約 185 天（3/31 前看 9/30 季報）。"""
    bs = _long(fetch("TaiwanStockBalanceSheet", stock_id))
    s = _pick(bs, SHARE_CAPITAL_TYPES)
    if s.empty:
        return None
    lag = np.where(s.index.month == 12, 90, 45)
    avail = s.index + pd.to_timedelta(lag, unit="D")
    s = s[avail <= _ts(as_of)]
    if not len(s) or (_ts(as_of) - s.index[-1]).days > max_age_days:
        return None
    return float(s.iloc[-1])


def cash_dividends_by_year(stock_id: str, as_of) -> pd.Series:
    """每個日曆年（依除息交易日）每股現金股利合計；只含除息日 ≤ as_of。
    季配息公司（如台積電）同一年多筆會加總。"""
    df = fetch("TaiwanStockDividend", stock_id)
    if df.empty or "CashEarningsDistribution" not in df.columns:
        return pd.Series(dtype=float)
    df = df.copy()
    cash = sum(pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)
               for c in ("CashEarningsDistribution", "CashStatutorySurplus"))
    ex = pd.to_datetime(df.get("CashExDividendTradingDate"), errors="coerce")
    ex = ex.fillna(pd.to_datetime(df["date"], errors="coerce"))
    s = pd.Series(cash.values, index=ex)
    s = s[(s > 0) & (s.index <= _ts(as_of))]
    return s.groupby(s.index.year).sum()


def valuation_at(stock_id: str, as_of, max_stale_days: int = 10) -> dict:
    """as_of 前最近一筆 PER / PBR / 殖利率；超過 max_stale_days 天沒資料視為無。"""
    df = fetch("TaiwanStockPER", stock_id)
    empty = {"per": None, "pbr": None, "dividend_yield": None}
    if df.empty:
        return empty
    df = df[pd.to_datetime(df["date"]) <= _ts(as_of)]
    if df.empty or (_ts(as_of) - pd.to_datetime(df["date"]).max()).days > max_stale_days:
        return empty
    row = df.sort_values("date").iloc[-1]

    def num(k):
        v = pd.to_numeric(row.get(k), errors="coerce")
        return None if pd.isna(v) else float(v)

    return {"per": num("PER"), "pbr": num("PBR"), "dividend_yield": num("dividend_yield")}


def price_history(stock_id: str, adjusted: bool = False) -> pd.DataFrame:
    """全歷史日 K（date, open, high, low, close）。adjusted=True 為除權息向後還原。"""
    df = fetch("TaiwanStockPrice", stock_id)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["close"] > 0].sort_values("date").reset_index(drop=True)
    if adjusted:
        df = adjust_for_dividends(df, dividend_factors(stock_id))
    return df


def dividend_factors(stock_id: str) -> pd.DataFrame:
    raw = fetch("TaiwanStockDividendResult", stock_id)
    if raw.empty or not {"before_price", "after_price"}.issubset(raw.columns):
        return pd.DataFrame(columns=["date", "factor"])
    ev = raw[["date", "before_price", "after_price"]].copy()
    ev["date"] = pd.to_datetime(ev["date"])
    for c in ("before_price", "after_price"):
        ev[c] = pd.to_numeric(ev[c], errors="coerce")
    ev = ev[(ev["before_price"] > 0) & (ev["after_price"] > 0)]
    ev["factor"] = ev["after_price"] / ev["before_price"]
    ev = ev[(ev["factor"] > 0.3) & (ev["factor"] < 1.0)]
    return ev[["date", "factor"]].sort_values("date").reset_index(drop=True)


def close_at(px: pd.DataFrame, as_of) -> float | None:
    sub = px[px["date"] <= _ts(as_of)]
    return float(sub["close"].iloc[-1]) if len(sub) else None


def index_history() -> pd.DataFrame:
    df = fetch("TaiwanStockPrice", "TAIEX")
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["date", "close"]].dropna().sort_values("date").reset_index(drop=True)


def beta_at(px_adj: pd.DataFrame, idx: pd.DataFrame, as_of, years: int = 2,
            min_weeks: int = 52) -> float | None:
    """週報酬 Beta（近 years 年，個股還原價 vs 加權指數）。"""
    end = _ts(as_of)
    start = end - pd.DateOffset(years=years)

    def weekly(df):
        s = df[(df["date"] > start) & (df["date"] <= end)].set_index("date")["close"]
        return s.resample("W-FRI").last().pct_change()

    if px_adj.empty or idx.empty:
        return None
    joined = pd.concat([weekly(px_adj), weekly(idx)], axis=1, keys=["s", "m"]).dropna()
    if len(joined) < min_weeks or joined["m"].var() == 0:
        return None
    return float(joined["s"].cov(joined["m"]) / joined["m"].var())
