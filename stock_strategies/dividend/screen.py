"""定存股篩選規則引擎。

策略檔放在 strategies/dividend/*.json，格式：
{
  "id": "...", "name": "...", "description": "...",
  "rules": { 規則名: 門檻, ... },          # 見 RULE_DOCS
  "portfolio": { "max_holdings": 18, "rebalance_month": 4, "hold_years": [3, 5] }
}

每條規則回 {"value", "threshold", "pass"}：
- pass=True/False：通過/不通過；資料缺 → False（寧可漏選，不可誤選）
- pass=None：此規則目前無資料可檢查（例如董監持股），不擋選股，只在報告註明
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .fundamentals import (
    annual_fundamentals, beta_at, cash_dividends_by_year, close_at,
    latest_fy, share_capital_at, valuation_at,
)

STRATEGY_DIR = Path(__file__).resolve().parents[2] / "strategies" / "dividend"

RULE_DOCS = {
    "min_share_capital_yi": "股本（億元）大於",
    "min_bvps": "每股淨值（元）大於",
    "min_price": "股價（元）大於",
    "min_roe": "最近年度 ROE（%）大於",
    "max_debt_ratio": "最近年度負債比（%）小於",
    "cash_div_years": "最近 N 個完整年度每年都配現金股利",
    "min_cash_div": "（搭配 cash_div_years）每年現金股利（元）大於",
    "eps_avg_years": "最近 N 個年度 EPS 平均",
    "min_eps_avg": "（搭配 eps_avg_years）EPS 平均（元）大於",
    "max_beta": "Beta（近 2 年週報酬）小於",
    "max_pbr": "股價淨值比小於",
    "min_director_holding": "董監持股（%）大於——FinMind 無資料，暫不檢查",
    "max_director_pledge": "董監質押（%）小於——FinMind 無資料，暫不檢查",
}


def load_strategies(directory: Path = STRATEGY_DIR) -> list[dict]:
    out = []
    for p in sorted(directory.glob("*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        unknown = set(s.get("rules", {})) - set(RULE_DOCS)
        if unknown:
            raise ValueError(f"{p.name} 有不認得的規則：{sorted(unknown)}")
        out.append(s)
    return out


def _chk(value, threshold, ok):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return {"value": None, "threshold": threshold, "pass": False}
    return {"value": round(float(value), 3), "threshold": threshold, "pass": bool(ok(value))}


class StockData:
    """單檔在某 as_of 的資料快照（同一檔多個策略共用，只抓一次）。"""

    def __init__(self, stock_id: str, as_of, px=None, px_adj=None, idx=None):
        self.stock_id = stock_id
        self.as_of = pd.Timestamp(as_of)
        self.fy = latest_fy(as_of)
        ann = annual_fundamentals(stock_id)
        self.annual = ann[ann.index <= self.fy] if not ann.empty else ann
        self.cash_div = cash_dividends_by_year(stock_id, as_of)
        self.valuation = valuation_at(stock_id, as_of)
        self.share_capital = share_capital_at(stock_id, as_of)
        self.price = close_at(px, as_of) if px is not None and len(px) else None
        self._px_adj, self._idx = px_adj, idx
        self._beta = "unset"

    def latest(self, col):
        if self.annual.empty or col not in self.annual or self.fy not in self.annual.index:
            return None
        v = self.annual.loc[self.fy, col]
        return None if pd.isna(v) else float(v)

    @property
    def beta(self):
        if self._beta == "unset":
            self._beta = (beta_at(self._px_adj, self._idx, self.as_of)
                          if self._px_adj is not None and self._idx is not None else None)
        return self._beta

    def eps_window(self, n):
        if self.annual.empty or "eps" not in self.annual:
            return None
        years = list(range(self.fy - n + 1, self.fy + 1))
        s = self.annual["eps"].reindex(years)
        return None if s.isna().any() else s

    def cash_window(self, n):
        last_full = self.as_of.year - 1           # 當年度還沒過完，不算
        years = list(range(last_full - n + 1, last_full + 1))
        return self.cash_div.reindex(years).fillna(0.0)


def evaluate_rules(d: StockData, rules: dict) -> dict:
    c = {}
    if "min_share_capital_yi" in rules:
        t = rules["min_share_capital_yi"]
        v = d.share_capital / 1e8 if d.share_capital else None
        c["股本(億)"] = _chk(v, t, lambda x: x > t)
    if "min_bvps" in rules:
        t = rules["min_bvps"]
        c["每股淨值"] = _chk(d.latest("bvps"), t, lambda x: x > t)
    if "min_price" in rules:
        t = rules["min_price"]
        c["股價"] = _chk(d.price, t, lambda x: x > t)
    if "min_roe" in rules:
        t = rules["min_roe"]
        c["ROE%"] = _chk(d.latest("roe"), t, lambda x: x > t)
    if "max_debt_ratio" in rules:
        t = rules["max_debt_ratio"]
        c["負債比%"] = _chk(d.latest("debt_ratio"), t, lambda x: x < t)
    if "cash_div_years" in rules:
        n, t = int(rules["cash_div_years"]), float(rules.get("min_cash_div", 0))
        w = d.cash_window(n)
        c[f"近{n}年每年現金股利"] = _chk(float(w.min()), t, lambda x: x > t)
    if "eps_avg_years" in rules:
        n, t = int(rules["eps_avg_years"]), float(rules.get("min_eps_avg", 0))
        w = d.eps_window(n)
        c[f"近{n}年EPS平均"] = _chk(None if w is None else float(w.mean()), t, lambda x: x > t)
    if "max_beta" in rules:
        t = rules["max_beta"]
        c["Beta"] = _chk(d.beta, t, lambda x: x < t)
    if "max_pbr" in rules:
        t = rules["max_pbr"]
        c["股價淨值比"] = _chk(d.valuation["pbr"], t, lambda x: x < t)
    for k, label in (("min_director_holding", "董監持股%"), ("max_director_pledge", "董監質押%")):
        if k in rules:
            c[label] = {"value": None, "threshold": rules[k], "pass": None}
    return c


def info_columns(d: StockData) -> dict:
    """不參與篩選、只供參考的欄位（含建議 B7：EPS 穩定度）。"""
    w = d.eps_window(5)
    cv = None
    if w is not None and w.mean() > 0:
        cv = round(float(w.std(ddof=0) / w.mean()), 2)
    return {
        "per": d.valuation["per"], "pbr": d.valuation["pbr"],
        "dividend_yield": d.valuation["dividend_yield"],
        "eps_cv_5y": cv,                  # 變異係數：越小越穩定，>0.5 屬景氣循環型
        "fy": d.fy,
    }


def screen(stocks: dict[str, StockData], strategy: dict, names: dict | None = None) -> pd.DataFrame:
    """回每檔一列：passed、各規則值、參考欄位；依 PER 由低到高排序。"""
    rows = []
    for sid, d in stocks.items():
        checks = evaluate_rules(d, strategy["rules"])
        passed = all(v["pass"] is not False for v in checks.values())
        failed = [k for k, v in checks.items() if v["pass"] is False]
        row = {"stock_id": sid, "name": (names or {}).get(sid, ""), "passed": passed,
               "failed": "、".join(failed)}
        row.update({k: v["value"] for k, v in checks.items()})
        row.update(info_columns(d))
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_per_key"] = df["per"].where(df["per"] > 0, np.inf)   # 無 PER / 虧損排最後
    return df.sort_values(["passed", "_per_key"], ascending=[False, True]).drop(columns="_per_key")


def select(screened: pd.DataFrame, max_holdings: int) -> pd.DataFrame:
    """論文規則：符合者依本益比由低到高，取前 max_holdings 檔。"""
    if screened.empty:
        return screened
    return screened[screened["passed"]].head(max_holdings).reset_index(drop=True)
