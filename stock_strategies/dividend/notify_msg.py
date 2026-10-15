"""定存股篩選結果 → 推播摘要（3d）。

訊息用 Telegram Markdown 撰寫（*粗體* / _斜體_），notify.send_notification 會依
NOTIFY_CHANNEL 送 Telegram 或轉成 Teams 卡片。

與前次比較：找同一策略、日期早於本次的最近一份 screen_<日期>_<策略id>.csv，
用相同規則（符合者依 PER 低→高取前 max_holdings 檔）還原前次名單，列出新增 / 移出。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_CSV_RE = re.compile(r"^screen_(\d{4}-\d{2}-\d{2})_(.+)\.csv$")


def _passed(df: pd.DataFrame) -> pd.Series:
    return df["passed"].astype(str).str.lower().isin(["true", "1"])


def selection_from_csv(path: Path, max_holdings: int) -> pd.DataFrame:
    """從 screen_*.csv 還原當次選入名單（csv 已依 passed、PER 排好序）。"""
    df = pd.read_csv(path, dtype={"stock_id": str})
    return df[_passed(df)].head(max_holdings).reset_index(drop=True)


def previous_csv(report_dir: Path, strategy_id: str, as_of: str) -> tuple[str, Path] | None:
    """同策略、日期早於 as_of 的最近一份篩選 csv（_v1 等備份檔名不符格式，自然排除）。"""
    best = None
    for p in report_dir.glob(f"screen_*_{strategy_id}.csv"):
        m = _CSV_RE.match(p.name)
        if not m or m.group(2) != strategy_id or m.group(1) >= as_of:
            continue
        if best is None or m.group(1) > best[0]:
            best = (m.group(1), p)
    return best


def _num(v, fmt):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return "—" if pd.isna(f) else format(f, fmt)


def format_dividend_message(as_of: str, fy: int, pool_size: int, sections: list[dict],
                            skipped: list[str] | None = None, report_name: str = "") -> str:
    """sections：每個策略一個 dict：name, n_pass, pick(DataFrame), prev(None 或 (日期, set(ids)))"""
    lines = [f"🏦 *定存股篩選 {as_of}*",
             f"財報年度 {fy} | 股票池 {pool_size} 檔 | 等權重、PER 低→高",
             "_每年 4 月調倉；其他時間的結果僅供追蹤_", ""]
    for s in sections:
        pick = s["pick"]
        lines.append(f"*{s['name']}* — 符合 {s['n_pass']} 檔，選入 {len(pick)} 檔")
        if len(pick) == 0:
            lines.append("（本次無符合標的）")
        for i, r in enumerate(pick.itertuples(index=False), 1):
            lines.append(f"{i}. {r.stock_id} {r.name}  PER {_num(r.per, '.1f')} | "
                         f"殖利率 {_num(r.dividend_yield, '.1f')}%")
        prev = s.get("prev")
        if prev is None:
            lines.append("_（首次篩選，無前次名單可比較）_")
        else:
            prev_date, prev_ids = prev
            now_ids = set(pick["stock_id"])
            names = dict(zip(pick["stock_id"], pick["name"]))
            names.update(s.get("prev_names", {}))
            added = sorted(now_ids - prev_ids)
            removed = sorted(prev_ids - now_ids)
            if not added and not removed:
                lines.append(f"↔️ 與 {prev_date} 名單相同")
            else:
                if added:
                    lines.append("🆕 新增：" + "、".join(f"{x} {names.get(x, '')}".strip() for x in added))
                if removed:
                    lines.append("❌ 移出：" + "、".join(f"{x} {names.get(x, '')}".strip() for x in removed))
                lines.append(f"_（對照 {prev_date}）_")
        lines.append("")
    if skipped:
        lines.append(f"⚠️ {len(skipped)} 檔資料讀取失敗未評估：{'、'.join(skipped)}（再跑一次 screen 可補上）")
    if report_name:
        lines.append(f"📄 完整報告：reports/dividend/{report_name}")   # 檔名含底線，不可包在 _斜體_ 裡
    lines.append("_僅供研究參考，非投資建議_")
    return "\n".join(lines)


def build_sections(report_dir: Path, as_of: str, strategies: list[dict]) -> list[dict]:
    """從本次（as_of）的 csv 與前次 csv 組出各策略段落。"""
    out = []
    for st in strategies:
        mh = st["portfolio"]["max_holdings"]
        cur_path = report_dir / f"screen_{as_of}_{st['id']}.csv"
        if not cur_path.exists():
            raise FileNotFoundError(f"找不到 {cur_path.name}，請先執行 dividend.py screen --as-of {as_of}")
        cur = pd.read_csv(cur_path, dtype={"stock_id": str})
        pick = cur[_passed(cur)].head(mh).reset_index(drop=True)
        sec = {"name": st["name"], "n_pass": int(_passed(cur).sum()), "pick": pick, "prev": None}
        prev = previous_csv(report_dir, st["id"], as_of)
        if prev:
            prev_pick = selection_from_csv(prev[1], mh)
            sec["prev"] = (prev[0], set(prev_pick["stock_id"]))
            sec["prev_names"] = dict(zip(prev_pick["stock_id"], prev_pick["name"]))
        out.append(sec)
    return out
