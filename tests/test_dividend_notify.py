"""3d：定存股篩選摘要推播。"""
import pandas as pd
import pytest

import dividend
from stock_strategies.dividend import notify_msg as N


def _csv(path, rows):
    pd.DataFrame(rows, columns=["stock_id", "name", "passed", "failed", "per", "dividend_yield"]).to_csv(
        path, index=False, encoding="utf-8-sig")


ST = [{"id": "dividend-emily", "name": "定存股・策略2", "portfolio": {"max_holdings": 2}},
      {"id": "dividend-sun", "name": "定存股・策略3", "portfolio": {"max_holdings": 18}}]


@pytest.fixture
def reports(tmp_path):
    # 前次（10-01）與本次（10-15）；另有 _v1 備份檔不可被當成前次
    _csv(tmp_path / "screen_2026-10-01_dividend-emily.csv",
         [("2539", "櫻花建", True, "", 8, 8.3), ("9910", "豐泰", True, "", 13, 6.0)])
    _csv(tmp_path / "screen_2026-10-15_dividend-emily_v1.csv",
         [("1111", "假的", True, "", 1, 1)])
    _csv(tmp_path / "screen_2026-10-15_dividend-emily.csv",
         [("2539", "櫻花建", True, "", 8.05, 8.26), ("2330", "台積電", True, "", 28.3, 0.9),
          ("2454", "聯發科", True, "", 68.8, 1.3), ("2412", "中華電", False, "ROE%", 30, 3.4)])
    _csv(tmp_path / "screen_2026-10-15_dividend-sun.csv",
         [("1102", "亞泥", True, "", 10.5, 6.1), ("2412", "中華電", False, "股價淨值比", 30, 3.4)])
    return tmp_path


def test_previous_csv_skips_backups_and_same_day(reports):
    d, p = N.previous_csv(reports, "dividend-emily", "2026-10-15")
    assert d == "2026-10-01" and p.name == "screen_2026-10-01_dividend-emily.csv"
    assert N.previous_csv(reports, "dividend-sun", "2026-10-15") is None


def test_message_lists_picks_and_changes(reports):
    secs = N.build_sections(reports, "2026-10-15", ST)
    msg = N.format_dividend_message("2026-10-15", 2025, 150, secs, skipped=["2887 台新金"],
                                    report_name="screen_2026-10-15.md")
    assert "*定存股・策略2* — 符合 3 檔，選入 2 檔" in msg      # max_holdings=2 截斷
    assert "1. 2539 櫻花建  PER 8.1 | 殖利率 8.3%" in msg
    assert "2. 2330 台積電" in msg and "聯發科" not in msg
    assert "🆕 新增：2330 台積電" in msg and "❌ 移出：9910 豐泰" in msg
    assert "首次篩選" in msg                                     # 策略3 無前次
    assert "2887 台新金" in msg and "非投資建議" in msg


def test_send_summary_skips_when_channel_not_configured(reports, monkeypatch, capsys):
    monkeypatch.setattr(dividend, "REPORT_DIR", reports)
    monkeypatch.setenv("NOTIFY_CHANNEL", "teams")
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    assert dividend.send_summary("2026-10-15", 150) is False
    assert "TEAMS_WEBHOOK_URL" in capsys.readouterr().err


def test_send_summary_sends(reports, monkeypatch):
    import stock_strategies.notify as notify
    from stock_strategies.dividend import screen as S
    monkeypatch.setattr(dividend, "REPORT_DIR", reports)
    monkeypatch.setattr(S, "load_strategies", lambda: ST)
    monkeypatch.setenv("NOTIFY_CHANNEL", "teams")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/x")
    sent = []
    monkeypatch.setattr(notify, "send_notification", lambda m: sent.append(m) or True)
    assert dividend.send_summary("2026-10-15", 150) is True
    assert len(sent) == 1 and "定存股篩選 2026-10-15" in sent[0]


# ── Google Sheet「Dividend」分頁 ───────────────────────────
def test_sheet_rows_marks_changes_and_watchlist(reports):
    secs = N.build_sections(reports, "2026-10-15", ST)
    rows = N.sheet_rows("2026-10-15", secs, {"2539": True, "9910": False})
    emily = [r for r in rows if r[1] == "策略2"]
    assert emily[0][:5] == ["2026-10-15", "策略2", 1, "2539", "櫻花建"]
    assert emily[0][8:] == ["持續", "已加入（啟用）"]
    assert emily[1][3] == "2330" and emily[1][8:] == ["新增", "未加入"]
    removed = [r for r in emily if str(r[8]).startswith("移出")]
    assert removed == [["2026-10-15", "策略2", "", "9910", "豐泰", "", "", "", "移出（2026-10-01 有）", "已加入（停用）"]]
    sun = [r for r in rows if r[1] == "策略3"]
    assert sun == [["2026-10-15", "策略3", 1, "1102", "亞泥", 10.5, 6.1, "", "首次", "未加入"]]


class _FakeWS:
    def __init__(self, values):
        self.values = values
        self.frozen = False

    def get_all_values(self):
        return self.values

    def clear(self):
        self.values = []

    def update(self, values, range_name, value_input_option=None):
        self.values = values

    def freeze(self, rows):
        self.frozen = True


class _FakeSh:
    def __init__(self, ws):
        self.ws = ws

    def worksheet(self, name):
        if self.ws is None:
            import gspread
            raise gspread.WorksheetNotFound(name)
        return self.ws

    def add_worksheet(self, title, rows, cols):
        self.ws = _FakeWS([])
        return self.ws


def test_write_dividend_picks_replaces_same_day(monkeypatch):
    from stock_strategies import sheet
    H = sheet.DIVIDEND_HEADERS
    ws = _FakeWS([H, ["2026-10-01", "策略2", "1", "2539"] + [""] * 6,
                     ["2026-10-15", "策略2", "1", "OLD"] + [""] * 6])
    monkeypatch.setattr(sheet, "get_gsheet", lambda: _FakeSh(ws))
    n = sheet.write_dividend_picks("2026-10-15", [["2026-10-15", "策略2", 1, "2330"] + [""] * 6])
    assert n == 1
    ids = [r[3] for r in ws.values[1:]]
    assert ids == ["2539", "2330"]            # 10-01 保留；10-15 舊列被取代
    assert ws.values[0] == H and ws.frozen


def test_write_dividend_picks_creates_sheet(monkeypatch):
    from stock_strategies import sheet
    sh = _FakeSh(None)
    monkeypatch.setattr(sheet, "get_gsheet", lambda: sh)
    sheet.write_dividend_picks("2026-10-15", [["2026-10-15", "策略3", 1, "1102"] + [""] * 6])
    assert sh.ws.values[0] == sheet.DIVIDEND_HEADERS and len(sh.ws.values) == 2


def test_record_to_sheet(reports, monkeypatch, capsys):
    import stock_strategies.sheet as sheet
    from stock_strategies.dividend import screen as S
    monkeypatch.setattr(dividend, "REPORT_DIR", reports)
    monkeypatch.setattr(S, "load_strategies", lambda: ST)
    monkeypatch.setenv("GOOGLE_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_CREDS_JSON", "{}")
    monkeypatch.setattr(sheet, "read_watchlist_status", lambda: {"2539": True})
    got = {}
    monkeypatch.setattr(sheet, "write_dividend_picks", lambda d, rows: got.update(d=d, rows=rows) or len(rows))
    assert dividend.record_to_sheet("2026-10-15") is True
    assert got["d"] == "2026-10-15" and len(got["rows"]) == 4        # 策略2：2 選入 + 1 移出；策略3：1
    assert "未加入 Watchlist 的選入股 2 檔" in capsys.readouterr().out


def test_record_to_sheet_skips_without_google_env(reports, monkeypatch, capsys):
    monkeypatch.setattr(dividend, "REPORT_DIR", reports)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    assert dividend.record_to_sheet("2026-10-15") is False
    assert "GOOGLE_SHEET_ID" in capsys.readouterr().err
