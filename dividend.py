"""定存股策略指令（第三階段）。

用法（在專案資料夾）：
  uv run python dividend.py pool                 # 建立／更新候選股票池（第一次約 2 小時）
  uv run python dividend.py screen               # 用兩套論文策略篩選，產生報告
  uv run python dividend.py screen --as-of 2026-04-01 --size 150
  uv run python dividend.py screen --no-notify   # 不推播
  uv run python dividend.py notify               # 用今天已產生的報告重送推播（不重跑）
  uv run python dividend.py notify --as-of 2026-10-15
  uv run python dividend.py sheet --as-of 2026-10-15   # 只把報告寫進 Google Sheet「Dividend」分頁

篩選完成後會依 .env 的 NOTIFY_CHANNEL（teams / telegram / both）推一則摘要，
含兩策略選入名單、殖利率、與前次名單的新增 / 移出。

報告寫到 reports/dividend/：
  pool_<日期>.csv、screen_<日期>.md、screen_<日期>_<策略>.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports" / "dividend"


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def get_pool(as_of: str, size: int, rebuild: bool = False):
    import pandas as pd
    from stock_strategies.dividend.pool import build_pool

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"pool_{as_of}.csv"
    if path.exists() and not rebuild:
        pool = pd.read_csv(path, dtype={"stock_id": str})
        if len(pool) >= size:
            print(f"使用既有股票池 {path.name}（{len(pool)} 檔）")
            return pool.head(size)
    print(f"建立股票池（as_of={as_of}，取股本前 {size} 檔）…")
    pool = build_pool(as_of, size=size)
    pool.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"股票池已存到 {path}（{len(pool)} 檔）")
    return pool


def _fmt(v, nd=2):
    if v is None:
        return "—"
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return "—"
    except Exception:
        pass
    if isinstance(v, bool):
        return "✅" if v else "❌"
    if isinstance(v, (int, float)):
        return f"{v:,.{nd}f}"
    return str(v)


def _md_table(df, cols):
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    return "\n".join(lines)


def send_summary(as_of: str, pool_size: int | None = None, skipped: list[str] | None = None) -> bool:
    """讀 as_of 的篩選 csv，組摘要並推播。推播設定不完整或失敗只警告、不中斷。"""
    import os
    from stock_strategies.dividend.fundamentals import latest_fy
    from stock_strategies.dividend.notify_msg import build_sections, format_dividend_message
    from stock_strategies.dividend.screen import load_strategies
    from stock_strategies.notify import required_notify_env, send_notification

    try:
        missing = [k for k in required_notify_env() if not os.environ.get(k)]
    except ValueError as e:
        print(f"⚠️ 未推播：{e}", file=sys.stderr)
        return False
    if missing:
        print(f"⚠️ 未推播：.env 缺少 {missing}", file=sys.stderr)
        return False
    if pool_size is None:
        pool_path = REPORT_DIR / f"pool_{as_of}.csv"
        pool_size = sum(1 for _ in open(pool_path, encoding="utf-8-sig")) - 1 if pool_path.exists() else 0
    sections = build_sections(REPORT_DIR, as_of, load_strategies())
    msg = format_dividend_message(as_of, latest_fy(as_of), pool_size, sections,
                                  skipped=skipped, report_name=f"screen_{as_of}.md")
    print("\n— 推播內容 —\n" + msg + "\n")
    if send_notification(msg):
        print("✅ 定存股摘要已推播")
        return True
    print("⚠️ 定存股摘要推播失敗（報告檔已產生，不受影響）", file=sys.stderr)
    return False


def record_to_sheet(as_of: str) -> bool:
    """把 as_of 的兩策略選股寫進 Google Sheet「Dividend」分頁（含與前次比較、是否已在 Watchlist）。
    Google 設定不完整或寫入失敗只警告、不中斷。不會修改 Watchlist。"""
    import os
    missing = [k for k in ("GOOGLE_SHEET_ID", "GOOGLE_CREDS_JSON") if not os.environ.get(k)]
    if missing:
        print(f"⚠️ 未寫入 Google Sheet：.env 缺少 {missing}", file=sys.stderr)
        return False
    try:
        from stock_strategies.dividend.notify_msg import build_sections, sheet_rows
        from stock_strategies.dividend.screen import load_strategies
        from stock_strategies.sheet import read_watchlist_status, write_dividend_picks
        sections = build_sections(REPORT_DIR, as_of, load_strategies())
        rows = sheet_rows(as_of, sections, read_watchlist_status())
        n = write_dividend_picks(as_of, rows)
    except Exception as e:
        print(f"⚠️ 寫入 Google Sheet 失敗：{type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
        return False
    todo = sum(1 for r in rows if r[-1] == "未加入" and not str(r[-2]).startswith("移出"))
    print(f"✅ 已寫入 Google Sheet「Dividend」分頁 {n} 列（未加入 Watchlist 的選入股 {todo} 檔）")
    return True


def cmd_screen(as_of: str, size: int, rebuild: bool = False, notify: bool = True,
               sheet: bool = True):
    import pandas as pd
    from stock_strategies.dividend import fundamentals as F
    from stock_strategies.dividend.screen import (
        RULE_DOCS, StockData, load_strategies, screen, select,
    )

    strategies = load_strategies()
    pool = get_pool(as_of, size, rebuild=rebuild)
    names = dict(zip(pool["stock_id"], pool["stock_name"]))
    idx = F.index_history()
    if idx.empty:
        print("⚠️ 抓不到加權指數，Beta 規則將全數判為不通過", file=sys.stderr)

    print(f"讀取 {len(pool)} 檔的財報、配息、估值、股價（第一次每檔約 5 次請求）…")
    data, skipped = {}, []
    for i, sid in enumerate(pool["stock_id"], 1):
        if i % 10 == 0 or i == len(pool):
            print(f"  {i}/{len(pool)}")
        try:
            px = F.price_history(sid)
            px_adj = F.price_history(sid, adjusted=True)
            data[sid] = StockData(sid, as_of, px=px, px_adj=px_adj, idx=idx)
        except Exception as e:
            print(f"  ⚠️ {sid} 資料讀取失敗，略過：{str(e)[:80]}")
            skipped.append((sid, names.get(sid, ""), str(e)[:80]))

    md = [f"# 定存股篩選報告（{as_of}）", "",
          f"- 股票池：上市普通股股本前 {len(pool)} 檔（as_of 當時已公布股本）",
          f"- 財報年度：{F.latest_fy(as_of)}（{as_of} 時已公布的最新年報）",
          "- 選股：符合全部規則者依本益比由低到高，最多取 max_holdings 檔、等權重",
          "- 「—」代表資料缺；資料缺的規則一律判不通過（寧可漏選，不可誤選）", ""]
    if skipped:
        md += [f"> ⚠️ **{len(skipped)} 檔資料讀取失敗、本次未評估**（多為網路逾時；"
               "再執行一次 `uv run python dividend.py screen` 即可補上，其餘走快取很快）：", ">"]
        md += [f"> - {sid} {nm}：{err}" for sid, nm, err in skipped]
        md.append("")
    for st in strategies:
        res = screen(data, st, names)
        pick = select(res, st["portfolio"]["max_holdings"])
        res.to_csv(REPORT_DIR / f"screen_{as_of}_{st['id']}.csv", index=False, encoding="utf-8-sig")
        rule_cols = [c for c in res.columns if c not in
                     {"stock_id", "name", "passed", "failed", "per", "pbr",
                      "dividend_yield", "eps_cv_5y", "fy"}]
        md += [f"## {st['name']}", "", st.get("description", ""), "", "**規則**", ""]
        md += [f"- {RULE_DOCS[k]}：{v}" for k, v in st["rules"].items()]
        n_pass = int(res["passed"].sum()) if len(res) else 0
        md += ["", f"**符合 {n_pass} 檔，選入 {len(pick)} 檔**（PER 低→高）", ""]
        if len(pick):
            md.append(_md_table(pick, ["stock_id", "name", "per", "dividend_yield", "pbr",
                                       *rule_cols, "eps_cv_5y"]))
        near = res[(~res["passed"]) & (res["failed"].str.count("、") == 0)].head(15)
        if len(near):
            md += ["", "**差一條就符合（參考）**", "",
                   _md_table(near, ["stock_id", "name", "failed", "per", "dividend_yield",
                                    *rule_cols])]
        md.append("")
        print(f"\n{st['name']}：符合 {n_pass} 檔，選入 {len(pick)} 檔")
        for _, r in pick.iterrows():
            print(f"  {r['stock_id']} {r['name']:<6} PER {_fmt(r['per'])}  殖利率 {_fmt(r['dividend_yield'])}%")

    md += ["## 說明", "",
           "- eps_cv_5y：近 5 年 EPS 變異係數（標準差/平均），越小越穩定；>0.5 屬景氣循環型，存股要小心（建議 B7，僅供參考不參與篩選）",
           "- 董監持股、董監質押：FinMind 無此資料，暫不檢查",
           "- 存活者偏差：股票池只含目前仍掛牌公司"]
    out = REPORT_DIR / f"screen_{as_of}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\n報告已寫到 {out}")
    if skipped:
        print(f"⚠️ {len(skipped)} 檔讀取失敗未評估：{', '.join(s for s, _, _ in skipped)}"
              "；再跑一次 screen 即可補上")
    if sheet:
        record_to_sheet(as_of)
    if notify:
        send_summary(as_of, len(pool), [f"{s} {n}".strip() for s, n, _ in skipped])


def main(argv=None):
    ap = argparse.ArgumentParser(description="定存股策略")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pool", "screen"):
        p = sub.add_parser(name)
        p.add_argument("--as-of", default=str(date.today()), help="決策日 YYYY-MM-DD，預設今天")
        p.add_argument("--size", type=int, default=150, help="股票池大小（股本前 N 檔）")
        p.add_argument("--rebuild", action="store_true", help="忽略既有池檔重建")
        if name == "screen":
            p.add_argument("--no-notify", action="store_true", help="篩選完不推播")
            p.add_argument("--no-sheet", action="store_true", help="篩選完不寫 Google Sheet")
    pn = sub.add_parser("notify", help="用已產生的篩選報告重送推播（不重跑篩選）")
    pn.add_argument("--as-of", default=str(date.today()), help="報告日期 YYYY-MM-DD，預設今天")
    ps = sub.add_parser("sheet", help="把已產生的篩選報告寫進 Google Sheet「Dividend」分頁")
    ps.add_argument("--as-of", default=str(date.today()), help="報告日期 YYYY-MM-DD，預設今天")
    args = ap.parse_args(argv)
    try:                                   # Windows 主控台 / 重導向時避免 emoji 編碼錯誤
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _load_env()
    if args.cmd == "notify":
        sys.exit(0 if send_summary(args.as_of) else 2)
    if args.cmd == "sheet":
        sys.exit(0 if record_to_sheet(args.as_of) else 2)
    from stock_strategies.dividend.fetch import enable_batch_pacing
    enable_batch_pacing()   # 每次打 API 間隔 ≥6.1 秒，不撞 FinMind 600 次/小時上限
    print("ℹ️ 已啟用控速（≤590 次/小時）；中斷後重跑會從快取接續。執行期間請勿同時跑每日系統。")
    if args.cmd == "pool":
        get_pool(args.as_of, args.size, rebuild=args.rebuild)
    else:
        cmd_screen(args.as_of, args.size, rebuild=args.rebuild, notify=not args.no_notify,
                   sheet=not args.no_sheet)


if __name__ == "__main__":
    main()
