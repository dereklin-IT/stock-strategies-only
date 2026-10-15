"""Stage 1 修正（A1~A4）的回歸測試。"""
import importlib

import pandas as pd

import main as main_mod
from stock_strategies import data, market
from tests.conftest import make_price_df

evaluate_mod = importlib.import_module("stock_strategies.evaluate")


# ── A3：除權息還原 ──────────────────────────────────────────
def test_adjust_for_dividends_scales_prices_before_ex_date():
    px = pd.DataFrame({
        "date": pd.to_datetime(["2024-07-01", "2024-07-02", "2024-07-03"]),
        "open": [100.0, 100.0, 95.0], "high": [101.0, 101.0, 96.0],
        "low": [99.0, 99.0, 94.0], "close": [100.0, 100.0, 95.0],
        "volume": [1, 1, 1],
    })
    # 7/3 除息 5 元：前收 100 → 參考價 95，factor 0.95
    ev = pd.DataFrame({"date": pd.to_datetime(["2024-07-03"]), "factor": [0.95]})
    out = data.adjust_for_dividends(px, ev)
    assert out["close"].tolist() == [95.0, 95.0, 95.0]   # 除息不再出現假跌幅
    assert out.iloc[-1]["close"] == 95.0                  # 最新價不變
    assert out["volume"].tolist() == [1, 1, 1]            # 量不調整


def test_adjust_for_dividends_compounds_multiple_events():
    px = make_price_df(n=5)
    ev = pd.DataFrame({"date": [px["date"][2], px["date"][4]], "factor": [0.9, 0.5]})
    out = data.adjust_for_dividends(px, ev)
    assert out["adj_factor"].round(4).tolist() == [0.45, 0.45, 0.5, 0.5, 1.0]


def test_get_dividend_events_filters_bad_rows(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-07-03", "2024-08-01", "2024-09-01"]),
        "before_price": [100.0, 50.0, 0.0],
        "after_price": [95.0, 60.0, 10.0],   # 第2筆 factor>1 異常、第3筆前價0
    })
    monkeypatch.setattr(data, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    ev = data.get_dividend_events("2330", "2024-01-01")
    assert len(ev) == 1 and abs(ev.iloc[0]["factor"] - 0.95) < 1e-9


def test_get_price_history_adjusted_falls_back_on_error(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [10, 11], "max": [10.5, 11.5], "min": [9.5, 10.5],
        "close": [10.2, 11.2], "Trading_Volume": [1000, 1100],
    })

    def fake(dataset, *a, **k):
        if dataset == "TaiwanStockDividendResult":
            raise RuntimeError("boom")
        return raw.copy()

    monkeypatch.setattr(data, "fetch_finmind_cached", fake)
    df = data.get_price_history("2330", 1, True)
    assert df.attrs["adjusted"] is False
    assert df.iloc[1]["close"] == 11.2


def test_evaluate_requests_adjusted_prices(monkeypatch):
    seen = {}

    def fake_px(sid, years, adjusted=False):
        seen["adjusted"] = adjusted
        return make_price_df()

    monkeypatch.setattr(evaluate_mod, "get_fundamental", lambda _: {"eps": {}, "roe": {}})
    monkeypatch.setattr(evaluate_mod, "get_price_history", fake_px)
    evaluate_mod.evaluate("2330", "t")
    assert seen["adjusted"] is True


# ── A4：EPS 只計四季齊全的年度 ─────────────────────────────
def test_get_fundamental_eps_skips_incomplete_year(monkeypatch):
    fs = pd.DataFrame({
        "date": pd.to_datetime(
            ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
             "2025-03-31", "2025-06-30", "2025-09-30"]),     # 2025 只有 3 季
        "type": ["EPS"] * 7,
        "value": [10.0] * 7,
    })
    monkeypatch.setattr(data, "fetch_finmind_cached",
                        lambda ds, *a, **k: fs.copy() if ds == "TaiwanStockFinancialStatements" else pd.DataFrame())
    monkeypatch.setattr(data, "datetime", type("D", (), {
        "now": staticmethod(lambda: pd.Timestamp("2026-02-15").to_pydatetime())}))
    f = data.get_fundamental("2330")
    assert f["eps"] == {2024: 40.0}   # 2025 不完整不採計（原本會算成 30 而誤判）


# ── A1/A2：main 讀策略、套用策略的大盤均線 ───────────────
def test_load_strategy_reads_file_and_falls_back():
    s = main_mod.load_strategy("conservative")
    assert s is not None and s["params"]["eps_threshold"] == 8.0
    assert main_mod.load_strategy("does-not-exist") is None


def test_market_filter_note_uses_period():
    results = [{"action": "BUY"}]
    n = market.apply_market_filter(results, {"bullish": False, "ma_period": 60})
    assert n == 1 and results[0]["risk_notes"] == ["大盤跌破 60 日線，自動降為 WATCH"]


def test_main_passes_strategy_and_ma_period(monkeypatch):
    calls = {"eval": [], "ma": None}
    for k in main_mod.REQUIRED_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("STRATEGY_ID", "s-cf230017")   # ma60 的策略
    monkeypatch.setattr(main_mod, "read_watchlist", lambda: [{"stock_id": "2330", "name": "台積電"}])

    def fake_market(period=20):
        calls["ma"] = period
        return {"bullish": True, "note": "ok"}

    monkeypatch.setattr(main_mod, "get_market_state", fake_market)
    monkeypatch.setattr(main_mod, "get_night_session", lambda: None)
    monkeypatch.setattr(main_mod, "night_filter_note", lambda n: "")
    monkeypatch.setattr(main_mod, "apply_night_filter", lambda r, n: 0)

    def fake_eval(sid, name, strategy=None):
        calls["eval"].append(strategy["id"])
        return {"stock_id": sid, "action": "WATCH", "signal_score": 60}

    monkeypatch.setattr(main_mod, "evaluate", fake_eval)
    monkeypatch.setattr(main_mod, "append_signals", lambda r: None)
    monkeypatch.setattr(main_mod, "read_performance", lambda: [])
    monkeypatch.setattr(main_mod, "update_performance", lambda e, r, **k: [])
    monkeypatch.setattr(main_mod, "write_performance", lambda p: None)
    monkeypatch.setattr(main_mod, "perf_summary", lambda p: {"count": 0})
    monkeypatch.setattr(main_mod, "format_messages", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "send_notification", lambda m: True)
    monkeypatch.setattr(main_mod, "required_notify_env", lambda: [])
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

    main_mod.main()
    assert calls["eval"] == ["s-cf230017"]
    assert calls["ma"] == 60
