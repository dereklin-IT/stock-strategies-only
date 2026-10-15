"""Telegram 發送失敗要能被偵測（公司防火牆回 HTML 封鎖頁的情境）。"""
import requests

from stock_strategies import notify


class _Resp:
    def __init__(self, status, text, js=None):
        self.status_code, self.text, self._js = status, text, js
        self.ok = 200 <= status < 300

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")


def test_success(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: _Resp(200, "", {"ok": True}))
    assert notify.send_telegram("hi") is True


def test_firewall_block_page(monkeypatch, capsys):
    _env(monkeypatch)
    html = "<html><h1>Application Blocked</h1>... has been blocked in accordance with company policy</html>"
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: _Resp(200, html))
    assert notify.send_telegram("hi") is False
    err = capsys.readouterr().err
    assert "防火牆" in err and "<html>" not in err


def test_telegram_api_error(monkeypatch, capsys):
    _env(monkeypatch)
    monkeypatch.setattr(notify.requests, "post",
                        lambda *a, **k: _Resp(400, "", {"ok": False, "description": "chat not found"}))
    assert notify.send_telegram("hi") is False
    assert "chat not found" in capsys.readouterr().err


def test_connection_error(monkeypatch):
    _env(monkeypatch)

    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(notify.requests, "post", boom)
    assert notify.send_telegram("hi") is False


# ── Teams 通道 ───────────────────────────────────────────
import pytest


def test_channel_selection(monkeypatch):
    monkeypatch.delenv("NOTIFY_CHANNEL", raising=False)
    assert notify.notify_channels() == ["telegram"]            # 預設維持舊行為
    monkeypatch.setenv("NOTIFY_CHANNEL", "Teams")
    assert notify.required_notify_env() == ["TEAMS_WEBHOOK_URL"]
    monkeypatch.setenv("NOTIFY_CHANNEL", "both")
    assert notify.notify_channels() == ["telegram", "teams"]
    monkeypatch.setenv("NOTIFY_CHANNEL", "line")
    with pytest.raises(ValueError):
        notify.notify_channels()


def test_teams_card_format():
    card = notify.teams_card("📊 *V3 每日選股報告*\n掃描 9 檔 | _參考_\n\n*2330 台積電*  綜合 60 分")
    body = card["attachments"][0]["content"]["body"]
    assert [b["text"] for b in body] == ["📊 **V3 每日選股報告**", "掃描 9 檔 | _參考_", "**2330 台積電**  綜合 60 分"]
    assert body[0]["weight"] == "Bolder"
    assert body[2]["spacing"] == "Medium"          # 空行 → 段落間距
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_send_teams_ok_and_fail(monkeypatch, capsys):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: _Resp(202, ""))
    assert notify.send_teams("hi") is True
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: _Resp(401, '{"error":"DirectApiAuthorizationRequired"}'))
    assert notify.send_teams("hi") is False
    assert "401" in capsys.readouterr().err


def test_send_notification_both(monkeypatch):
    monkeypatch.setenv("NOTIFY_CHANNEL", "both")
    calls = []
    monkeypatch.setattr(notify, "send_telegram", lambda t: calls.append("tg") or False)
    monkeypatch.setattr(notify, "send_teams", lambda t: calls.append("teams") or True)
    assert notify.send_notification("hi") is False      # 任一失敗 → False
    assert calls == ["tg", "teams"]                    # 但兩個都有送
