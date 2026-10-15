import os
import time
from datetime import datetime, timedelta

import requests
import pandas as pd

from .config import FINMIND_URL
from .cache import fetch_finmind_cached


def fetch_finmind(
    dataset: str,
    stock_id: str,
    start_date: str,
    timeout: int = 30,
    max_retries: int = 2,
) -> pd.DataFrame:
    """FinMind GET with retry + timeout 30s.
    對 timeout / connection error 自動 retry，間隔 exponential backoff (1s, 2s)。
    """
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "token": os.environ["FINMIND_TOKEN"],
    }
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(FINMIND_URL, params=params, timeout=timeout)
            r.raise_for_status()
            return pd.DataFrame(r.json().get("data", []))
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as e:
            last_err = e
            if attempt < max_retries:
                wait = 1.0 * (2 ** attempt)
                print(
                    f"[finmind] {dataset}/{stock_id} {type(e).__name__}, "
                    f"retry {attempt + 1}/{max_retries} after {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            raise
    if last_err:
        raise last_err
    return pd.DataFrame()


def get_price_history(stock_id: str, years: int = 3, adjusted: bool = False) -> pd.DataFrame:
    """個股日 K。

    adjusted=True → 依除權息結果做「向後還原」（最新價不變，過去價格依除權息參考價比例縮放），
    避免除息日的假跌幅誤觸停損、並讓報酬包含股利。還原失敗時回原始價，
    並以 df.attrs["adjusted"]=False 標記，供上層加註風險。
    """
    start = (datetime.now() - timedelta(days=365 * years + 60)).strftime("%Y-%m-%d")
    df = fetch_finmind_cached("TaiwanStockPrice", stock_id, start)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    if adjusted:
        try:
            events = get_dividend_events(stock_id, start)
            df = adjust_for_dividends(df, events)
            df.attrs["adjusted"] = True
        except Exception as e:
            print(f"[data] {stock_id} 除權息還原失敗，改用原始價: {str(e)[:80]}")
            df.attrs["adjusted"] = False
    return df


def get_dividend_events(stock_id: str, start: str) -> pd.DataFrame:
    """除權息事件（FinMind TaiwanStockDividendResult）。
    回 date(除權息交易日), factor(= 除權息參考價 / 除權息前收盤，0~1)。"""
    raw = fetch_finmind_cached("TaiwanStockDividendResult", stock_id, start)
    if raw.empty or not {"date", "before_price", "after_price"}.issubset(raw.columns):
        return pd.DataFrame(columns=["date", "factor"])
    ev = raw[["date", "before_price", "after_price"]].copy()
    ev["date"] = pd.to_datetime(ev["date"])
    ev["before_price"] = pd.to_numeric(ev["before_price"], errors="coerce")
    ev["after_price"] = pd.to_numeric(ev["after_price"], errors="coerce")
    ev = ev[(ev["before_price"] > 0) & (ev["after_price"] > 0)]
    ev["factor"] = ev["after_price"] / ev["before_price"]
    # 合理範圍：參考價不可能高於前收；低於 0.3 視為異常資料（例如減資、資料錯誤）略過
    ev = ev[(ev["factor"] > 0.3) & (ev["factor"] < 1.0)]
    return ev[["date", "factor"]].sort_values("date").reset_index(drop=True)


def adjust_for_dividends(px: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """向後還原：除權息日 d 之前的 OHLC 乘上 d 的 factor（多次事件累乘）。
    成交量不調整。新增 adj_factor 欄位方便檢查。"""
    out = px.copy()
    out["adj_factor"] = 1.0
    if events is None or events.empty:
        return out
    dates = pd.to_datetime(out["date"])
    for _, ev in events.iterrows():
        out.loc[dates < ev["date"], "adj_factor"] *= float(ev["factor"])
    for col in ["open", "high", "low", "close"]:
        if col in out.columns:
            out[col] = out[col] * out["adj_factor"]
    return out


def get_fundamental(stock_id: str) -> dict:
    """近 3 完整年度 EPS、ROE。

    EPS = 該年單季 EPS 加總（年度）。
    ROE = 年度淨利 / 年底歸屬母公司權益 × 100——FinMind 財報無 ROE 欄，故自算。
    """
    start = f"{datetime.now().year - 4}-01-01"
    fs = fetch_finmind_cached("TaiwanStockFinancialStatements", stock_id, start)
    if fs.empty:
        return {"eps": {}, "roe": {}}

    fs = fs.copy()
    fs["date"] = pd.to_datetime(fs["date"])
    fs["year"] = fs["date"].dt.year
    fs["value"] = pd.to_numeric(fs["value"], errors="coerce")

    # 只採計「4 季 EPS 都已公布」的年度；年初去年 Q4 未公布時，避免 3 季加總被當全年而低估
    eps_rows = fs[fs["type"] == "EPS"]
    eps_sum = eps_rows.groupby("year")["value"].sum()
    eps_cnt = eps_rows.groupby("year")["value"].count()
    eps = {y: v for y, v in eps_sum.items() if eps_cnt.get(y, 0) >= 4}
    roe = _compute_roe(fs, stock_id, start)

    cy = datetime.now().year
    return {
        "eps": {y: round(v, 2) for y, v in eps.items() if cy - 3 <= y < cy},
        "roe": {y: round(v, 2) for y, v in roe.items() if cy - 3 <= y < cy},
    }


# 淨利科目優先序：歸屬母公司淨利 → 稅後淨利（一般 / 金融業命名不同）→ 舊版用的綜合損益總額
NET_INCOME_TYPES = [
    "EquityAttributableToOwnersOfParent",      # 損益表中＝本期淨利歸屬母公司業主
    "IncomeAfterTaxes", "IncomeAfterTax",
    "TotalConsolidatedProfitForThePeriod",     # 實為「綜合損益總額」，含 OCI，僅作最後備援
]
# 權益科目優先序：歸屬母公司權益 → 權益總額（金融業資產負債表常無前者）
EQUITY_TYPES = ["EquityAttributableToOwnersOfParent", "Equity"]


def _first_type(df: pd.DataFrame, candidates: list[str]) -> pd.DataFrame:
    for t in candidates:
        sub = df[df["type"] == t]
        if not sub.empty:
            return sub
    return df.iloc[0:0]


def _compute_roe(fs: pd.DataFrame, stock_id: str, start: str) -> dict:
    """ROE = 年度歸屬母公司淨利 / 年底歸屬母公司權益 × 100。

    修正（第三階段 A8）：
    - 分子原用 TotalConsolidatedProfitForThePeriod，實為「綜合損益總額」（含其他綜合損益），
      兆豐金一季就差到 2 倍以上；改用歸屬母公司淨利。
    - 分母原只找 EquityAttributableToOwnersOfParent，金融股資產負債表沒有此科目 →
      ROE 永遠算不出、金融股一律「基本面資料不足」；改為退用 Equity。
    只對「該年 4 季淨利齊全」的年度計算。
    """
    ni = _first_type(fs, NET_INCOME_TYPES)
    if ni.empty:
        return {}
    bs = fetch_finmind_cached("TaiwanStockBalanceSheet", stock_id, start)
    if bs.empty or "type" not in bs.columns:
        return {}
    bs = bs.copy()
    bs["date"] = pd.to_datetime(bs["date"])
    bs["year"] = bs["date"].dt.year
    bs["value"] = pd.to_numeric(bs["value"], errors="coerce")
    eq = _first_type(bs, EQUITY_TYPES).sort_values("date")
    if eq.empty:
        return {}

    ni_sum = ni.groupby("year")["value"].sum()
    ni_cnt = ni.groupby("year")["value"].count()
    eq_year_end = eq.groupby("year")["value"].last()   # 該年最後一筆＝年底權益

    roe = {}
    for y, profit in ni_sum.items():
        eq_y = eq_year_end.get(y)
        if ni_cnt.get(y, 0) >= 4 and eq_y and eq_y > 0:
            roe[int(y)] = profit / eq_y * 100
    return roe
