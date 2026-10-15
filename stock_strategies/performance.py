"""訊號效度追蹤（成績單）

每天系統發 BUY 後，追蹤該訊號在 T+5 / T+10 / T+20 個交易日的實際表現，
累積一段時間後就能知道系統到底準不準。

追蹤欄位：
- signal_date: 訊號產生日（T）
- entry_close: T 日收盤（參考價）
- entry_open:  T+1 日開盤（實際進場價）
- t5_/t10_/t20_ret: 以 entry_open 為基準的實際漲跌幅（%）
- hit_target/hit_stop: 持有期內是否觸及策略停利 / 停損（預設 +10% / -8%）
- status: 追蹤中 / 完成
"""

from datetime import datetime

import pandas as pd

from .config import CONFIG
from .data import get_price_history


CHECKPOINTS = (5, 10, 20)


def _parse_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def update_performance(
    existing: list[dict],
    today_signals: list[dict],
    target_return: float | None = None,
    stop_loss: float | None = None,
) -> list[dict]:
    """補齊舊訊號的 T+1/T+5/T+10/T+20，再附上今日新 BUY。

    回傳要寫回 Google Sheet 的完整紀錄清單。
    """
    records = [dict(r) for r in existing]  # 拷貝一份避免 side effect
    target = CONFIG["target_return"] if target_return is None else target_return
    stop = CONFIG["stop_loss"] if stop_loss is None else stop_loss

    # 1. 依 stock_id 分組，對每檔只抓一次歷史價
    unfinished_by_stock: dict[str, list[dict]] = {}
    for r in records:
        if str(r.get("status", "")).strip() == "完成":
            continue
        sid = str(r.get("stock_id", "")).strip()
        if sid:
            unfinished_by_stock.setdefault(sid, []).append(r)

    for sid, recs in unfinished_by_stock.items():
        try:
            px = get_price_history(sid, years=1)
        except Exception:
            continue
        if px.empty:
            continue
        px = px.sort_values("date").reset_index(drop=True)
        px["date_str"] = px["date"].dt.strftime("%Y-%m-%d")
        date_to_iloc = {d: i for i, d in enumerate(px["date_str"].tolist())}

        for r in recs:
            sd = str(r.get("signal_date", "")).strip()
            entry_day_iloc = date_to_iloc.get(sd)
            if entry_day_iloc is None:
                continue  # 還沒抓到訊號日（可能資料還沒更新）

            # --- T+1 開盤（實際進場價）---
            if not r.get("entry_open"):
                if entry_day_iloc + 1 < len(px):
                    t1_open = px.iloc[entry_day_iloc + 1].get("open")
                    if t1_open is not None and not pd.isna(t1_open):
                        r["entry_open"] = round(float(t1_open), 2)

            entry_open = _parse_float(r.get("entry_open"))
            if not entry_open or entry_open <= 0:
                continue

            # --- T+N 收盤 & 報酬率 ---
            for n in CHECKPOINTS:
                col_date = f"t{n}_date"
                col_close = f"t{n}_close"
                col_ret = f"t{n}_ret"
                if _parse_float(r.get(col_ret)) is not None:
                    continue
                target_iloc = entry_day_iloc + 1 + n  # T+1 進場，再過 N 天
                if target_iloc >= len(px):
                    continue
                row = px.iloc[target_iloc]
                r[col_date] = row["date_str"]
                r[col_close] = round(float(row["close"]), 2)
                r[col_ret] = round(
                    (float(row["close"]) / entry_open - 1) * 100, 2
                )

            # --- 是否觸及停利/停損（20 日內）---
            window_start = entry_day_iloc + 1
            window_end = min(entry_day_iloc + 1 + 20, len(px))
            window = px.iloc[window_start:window_end]
            if len(window) > 0:
                hi = float(window["high"].max())
                lo = float(window["low"].min())
                # 只在還沒標記過時補上，讓結果單調不來回跳
                if not r.get("hit_target"):
                    r["hit_target"] = (
                        "Y" if hi >= entry_open * (1 + target) else ""
                    )
                if not r.get("hit_stop"):
                    r["hit_stop"] = (
                        "Y" if lo <= entry_open * (1 - stop) else ""
                    )
                # 追蹤滿 20 天才把空的明確標 N
                if len(window) >= 20:
                    if r.get("hit_target") != "Y":
                        r["hit_target"] = "N"
                    if r.get("hit_stop") != "Y":
                        r["hit_stop"] = "N"

            # 都填好就標完成
            if _parse_float(r.get(f"t{CHECKPOINTS[-1]}_ret")) is not None:
                r["status"] = "完成"

    # 2. 把今日新 BUY 加入追蹤清單
    today = datetime.now().strftime("%Y-%m-%d")
    existing_keys = {
        (str(r.get("signal_date", "")), str(r.get("stock_id", "")))
        for r in records
    }
    for s in today_signals:
        if s.get("action") != "BUY":
            continue
        sig_date = str(s.get("date", today))
        sid = str(s.get("stock_id", ""))
        if (sig_date, sid) in existing_keys:
            continue
        records.append({
            "signal_date": sig_date,
            "stock_id": sid,
            "name": s.get("name", ""),
            "entry_close": s.get("entry_price", ""),
            "entry_open": "",
            "t5_date": "", "t5_close": "", "t5_ret": "",
            "t10_date": "", "t10_close": "", "t10_ret": "",
            "t20_date": "", "t20_close": "", "t20_ret": "",
            "hit_target": "", "hit_stop": "",
            "status": "追蹤中",
        })

    return records


def summary(records: list[dict]) -> dict:
    """統計已完成的訊號表現"""
    finished = [r for r in records if str(r.get("status", "")).strip() == "完成"]
    if not finished:
        return {
            "count": 0,
            "winrate_t20": None,
            "avg_t20": None,
            "hit_target": 0,
            "hit_stop": 0,
        }
    rets = [_parse_float(r.get("t20_ret")) for r in finished]
    rets = [x for x in rets if x is not None]
    wins = sum(1 for x in rets if x > 0)
    hit_t = sum(1 for r in finished if str(r.get("hit_target", "")).upper() == "Y")
    hit_s = sum(1 for r in finished if str(r.get("hit_stop", "")).upper() == "Y")
    return {
        "count": len(finished),
        "winrate_t20": round(wins / len(rets) * 100, 1) if rets else None,
        "avg_t20": round(sum(rets) / len(rets), 2) if rets else None,
        "hit_target": hit_t,
        "hit_stop": hit_s,
    }
