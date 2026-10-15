"""第三階段 3a：探測 FinMind 各資料表的欄位格式（只讀取，不改任何檔案）。

用法：uv run python tools/probe_finmind.py
結果寫到 _review/probe.txt（UTF-8）。
抓到的資料會進快取（.cache/finmind），之後回測可直接沿用、不浪費額度。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))            # 讓 tools/ 下的腳本找得到 stock_strategies
OUT = ROOT / "_review" / "probe.txt"
OUT.parent.mkdir(exist_ok=True)
_console = sys.stdout
sys.stdout = open(OUT, "w", encoding="utf-8")   # 直接寫 UTF-8，避免 PowerShell 轉成 UTF-16

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import pandas as pd

from stock_strategies.cache import fetch_finmind_cached

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 40)

# 2412 中華電（電信）、2330 台積電（一般）、2886 兆豐金（金融：財報科目不同）、2105 正新（有配股）
STOCKS = ["2412", "2330", "2886", "2105"]


def show(title, df, type_col=None):
    print(f"\n{'=' * 100}\n## {title}  rows={len(df)}")
    if df.empty:
        print("（空）")
        return
    print("columns:", list(df.columns))
    if type_col and type_col in df.columns:
        last = df.sort_values("date").groupby(type_col).tail(1)
        print(f"-- 各 {type_col} 最新一筆（{len(last)} 種）--")
        print(last[["date", type_col, "value"]].to_string(index=False))
    else:
        print(df.tail(6).to_string(index=False))


def safe(dataset, sid, start):
    try:
        return fetch_finmind_cached(dataset, sid, start)
    except Exception as e:
        print(f"\n!! {dataset} {sid} 失敗: {type(e).__name__}: {str(e)[:150]}")
        return pd.DataFrame()


info = safe("TaiwanStockInfo", "", "1990-01-01")
show("TaiwanStockInfo（全市場）", info)
if not info.empty:
    for c in ("type", "industry_category"):
        if c in info.columns:
            print(f"\n-- {c} 分布 --")
            print(info[c].value_counts().head(40).to_string())

for sid in STOCKS:
    print(f"\n\n{'#' * 100}\n# {sid}\n{'#' * 100}")
    show(f"{sid} TaiwanStockBalanceSheet", safe("TaiwanStockBalanceSheet", sid, "2023-01-01"), "type")
    show(f"{sid} TaiwanStockFinancialStatements", safe("TaiwanStockFinancialStatements", sid, "2024-01-01"), "type")
    show(f"{sid} TaiwanStockDividend", safe("TaiwanStockDividend", sid, "2019-01-01"))
    show(f"{sid} TaiwanStockDividendResult", safe("TaiwanStockDividendResult", sid, "2019-01-01"))
    show(f"{sid} TaiwanStockPER", safe("TaiwanStockPER", sid, "2026-09-01"))

print("\n\n完成")
sys.stdout.close()
sys.stdout = _console
print(f"完成，結果已寫到 {OUT}")
