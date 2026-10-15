"""Stage 2 修正（A5~A7）的回歸測試。"""
import pandas as pd
import pytest

import main as main_mod
from stock_strategies.backtest import backtest
from stock_strategies.evaluate import fundamental_score
from stock_strategies.loader import merge_params, validate_strategy, StrategyError
from stock_strategies.notify import format_messages
from stock_strategies.performance import update_performance
from stock_strategies import performance


def _flat_df(n=100, close=100.0, last_close=None):
    """平盤序列；最後收盤可指定，用來精確控制到期報酬。"""
    closes = [close] * n
    if last_close is not None:
        closes[-1] = last_close
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "open": [close] * n, "high": [c + 0.01 for c in closes],
        "low": [c - 0.01 for c in closes], "close": closes,
        "ma20": [50.0] * n, "ma60": [40.0] * n,   # close>ma20>ma60 → 均線訊號必觸發
        "bb_lower": [float("nan")] * n, "bb_mid": [float("nan")] * n,
        "k": [float("nan")] * n, "d": [float("nan")] * n,
        "macd_hist": [float("nan")] * n, "dif": [float("nan")] * n, "dea": [float("nan")] * n,
    })


ONLY_MA = {"use_ma_alignment": True, "use_bollinger_bounce": False,
           "use_kd_golden_cross": False, "use_macd_bullish": False,
           "min_tech_score_for_signal": 50, "hold_days": 5}


# ── A5：交易成本 ───────────────────────────────────────
def test_backtest_flat_price_is_a_loss_after_costs():
    bt = backtest(_flat_df(), ONLY_MA)
    assert bt["winrate"] == 0.0                     # 不漲不跌，扣成本後全是虧
    assert bt["avg_return"] == pytest.approx(-0.00585, abs=1e-4)


def test_backtest_cost_can_be_zero():
    bt = backtest(_flat_df(), {**ONLY_MA, "round_trip_cost": 0})
    assert bt["avg_return"] == pytest.approx(0.0, abs=1e-6)


def test_round_trip_cost_param_validated():
    assert merge_params(None)["round_trip_cost"] == pytest.approx(0.00585)
    with pytest.raises(StrategyError):
        validate_strategy({"name": "x", "params": {"round_trip_cost": 0.2}})


# ── A6：連續基本面分數 ─────────────────────────────────
P = {"eps_threshold": 5.0, "roe_threshold": 15.0}


def test_fundamental_score_scale():
    assert fundamental_score(None, None, P, False) == 40      # 無資料：維持舊值
    assert fundamental_score(5.01, 15.01, P, True) == pytest.approx(80, abs=0.1)
    assert fundamental_score(10, 30, P, True) == 100           # 2 倍門檻滿分
    assert fundamental_score(20, 60, P, True) == 100           # 超過 2 倍封頂
    mid = fundamental_score(7.5, 22.5, P, True)
    assert mid == 90


def test_fundamental_score_fail_capped_below_pass():
    # EPS 很高但 ROE 沒過 → 不可高於 60，必低於任何通過者(≥80)
    assert fundamental_score(50, 10, P, False) == 60
    assert fundamental_score(2.5, 7.5, P, False) == 30
    assert fundamental_score(-3, 7.5, P, False) == 15         # 虧損 EPS 那項 0 分


# ── A7：顯示 / 追蹤跟著策略 ───────────────────────────
def test_rules_block_follows_strategy_params():
    p = merge_params({"params": {"eps_threshold": 8, "roe_threshold": 20,
                                 "stop_loss": 0.05, "target_return": 0.08,
                                 "hold_days": 15, "min_total_score_for_buy": 75,
                                 "weight_fundamental": 0.4, "weight_technical": 0.25,
                                 "weight_backtest": 0.35}})
    text = "\n".join(format_messages([], params=p))
    assert "EPS>8,ROE>20" in text
    assert "基本面40% + 技術25% + 回測35%" in text
    assert "BUY≥75" in text
    assert "停損5% / 停利8% / 持有15日" in text


def test_stock_detail_uses_signal_own_stop_target():
    s = {"stock_id": "2330", "name": "t", "action": "BUY", "signal_score": 80,
         "entry_price": 100, "stop_loss_price": 95, "target_price": 108,
         "stop_loss_pct": 0.05, "target_pct": 0.08,
         "risk_reward_ratio": 1.6, "position_size_pct": 20,
         "components": {"backtest_winrate": 0.6, "backtest_samples": 10,
                        "fundamental_pass": True, "fundamental_score": 92}}
    text = "\n".join(format_messages([s]))
    assert "停損 95 (-5%)" in text and "目標 108 (+8%)" in text
    assert "基本面✅92" in text


def test_performance_uses_strategy_stop(monkeypatch):
    # 進場 100，期間最低 94：預設 -8% 不算觸及，策略 -5% 要算觸及
    n = 30
    px = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=n),
                       "open": [100.0] * n, "high": [101.0] * n,
                       "low": [100.0] * 5 + [94.0] + [100.0] * (n - 6),
                       "close": [100.0] * n})
    monkeypatch.setattr(performance, "get_price_history", lambda *a, **k: px.copy())
    rec = [{"signal_date": "2024-01-01", "stock_id": "2330", "status": "追蹤中"}]
    assert update_performance(rec, [])[0]["hit_stop"] == "N"
    assert update_performance(rec, [], target_return=0.08, stop_loss=0.05)[0]["hit_stop"] == "Y"


def test_perf_message_uses_params():
    msg = main_mod._format_perf_message(
        {"count": 5, "winrate_t20": 60, "avg_t20": 1.2, "hit_target": 2, "hit_stop": 1},
        merge_params({"params": {"stop_loss": 0.05, "target_return": 0.08}}))
    assert "觸及停利 +8%" in msg and "觸及停損 -5%" in msg
