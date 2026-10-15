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
