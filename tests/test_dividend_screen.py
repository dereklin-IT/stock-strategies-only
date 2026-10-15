"""第三階段 3b：定存股資料層 / 股票池 / 篩選規則 / ROE 修正 測試。
假資料格式依 tools/probe_finmind.py 實測欄位。"""
import numpy as np
import pandas as pd
import pytest

from stock_strategies import data as data_mod
from stock_strategies.dividend import fetch as fetch_mod
from stock_strategies.dividend import fundamentals as F
from stock_strategies.dividend import pool as pool_mod
from stock_strategies.dividend import screen as S
from stock_strategies.cache import FinMindRateLimitError


# ── 假 FinMind ────────────────────────────────────────────
def _quarters(years):
    return [pd.Timestamp(y, m, d) for y in years for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]


def make_fs(eps_per_q=1.5, ni_per_q=1e9, years=range(2019, 2026), bank=False):
    rows = []
    for d in _quarters(years):
        rows.append({"date": d, "type": "EPS", "value": eps_per_q})
        rows.append({"date": d, "type": "EquityAttributableToOwnersOfParent", "value": ni_per_q})
        rows.append({"date": d, "type": "IncomeAfterTax" if bank else "IncomeAfterTaxes", "value": ni_per_q * 1.05})
        # 綜合損益總額故意設很大：確認不再被當成淨利
        rows.append({"date": d, "type": "TotalConsolidatedProfitForThePeriod", "value": ni_per_q * 2.2})
    return pd.DataFrame(rows)


def make_bs(equity=25e9, liab=10e9, capital=8e9, years=range(2019, 2027), bank=False):
    rows = []
    for d in _quarters(years):
        if not bank:   # 金融股沒有歸屬母公司權益科目
            rows.append({"date": d, "type": "EquityAttributableToOwnersOfParent", "value": equity})
        rows.append({"date": d, "type": "Equity", "value": equity * 1.02})
        rows.append({"date": d, "type": "Liabilities", "value": liab})
        rows.append({"date": d, "type": "TotalAssets", "value": liab + equity * 1.02})
        rows.append({"date": d, "type": "OrdinaryShare", "value": capital})
    return pd.DataFrame(rows)


def make_div(cash=2.0, years=range(2019, 2026), quarterly=False):
    rows = []
    for y in years:
        dates = [pd.Timestamp(y, m, 15) for m in (3, 6, 9, 12)] if quarterly else [pd.Timestamp(y, 7, 10)]
        for d in dates:
            rows.append({"date": d, "year": f"{y - 1912}年", "CashEarningsDistribution": cash / len(dates),
                         "CashStatutorySurplus": 0.0, "CashExDividendTradingDate": d.strftime("%Y-%m-%d"),
                         "StockEarningsDistribution": 0.0})
    return pd.DataFrame(rows)


def make_per(per=12.0, pbr=1.5, dy=5.0, start="2025-01-01", end="2026-10-14"):
    d = pd.bdate_range(start, end)
    return pd.DataFrame({"date": d, "PER": per, "PBR": pbr, "dividend_yield": dy})


def make_px(start="2023-01-02", end="2026-10-14", base=50.0, beta=0.5, seed=1):
    d = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, len(d))
    s = beta * m + rng.normal(0, 0.003, len(d))
    return (pd.DataFrame({"date": d, "close": base * np.cumprod(1 + s)}),
            pd.DataFrame({"date": d, "close": 10000 * np.cumprod(1 + m)}))


@pytest.fixture
def fake_finmind(monkeypatch):
    store = {}

    def fake_fetch(dataset, data_id, start=None, **k):
        return store.get((dataset, data_id), pd.DataFrame()).copy()

    monkeypatch.setattr(F, "fetch", fake_fetch)
    monkeypatch.setattr(pool_mod, "fetch", fake_fetch)
    return store


# ── point-in-time 規則 ───────────────────────────────────
def test_latest_fy_respects_annual_report_deadline():
    assert F.latest_fy("2026-03-30") == 2024      # 年報 3/31 才截止
    assert F.latest_fy("2026-04-01") == 2025
    assert F.latest_fy("2026-10-15") == 2025


def test_share_capital_publication_lag(fake_finmind):
    bs = pd.DataFrame({"date": pd.to_datetime(["2025-09-30", "2025-12-31"]),
                       "type": ["OrdinaryShare"] * 2, "value": [1e9, 2e9]})
    fake_finmind[("TaiwanStockBalanceSheet", "X")] = bs
    assert F.share_capital_at("X", "2025-11-13") is None   # 9/30 + 45 天 = 11/14 才公開 → 還看不到
    assert F.share_capital_at("X", "2025-11-14") == 1e9
    assert F.share_capital_at("X", "2026-03-30") == 1e9    # 12/31 年報 +90 天前看不到
    assert F.share_capital_at("X", "2026-03-31") == 2e9


def test_cash_dividends_calendar_year_and_quarterly_sum(fake_finmind):
    fake_finmind[("TaiwanStockDividend", "Q")] = make_div(cash=4.0, years=[2024, 2025], quarterly=True)
    s = F.cash_dividends_by_year("Q", "2025-10-01")
    assert s[2024] == pytest.approx(4.0)                     # 季配 4 次加總
    assert s[2025] == pytest.approx(3.0)                     # 12 月那次還沒除息


# ── 年度基本面 + ROE 修正 ───────────────────────────────
def test_annual_fundamentals_values(fake_finmind):
    fake_finmind[("TaiwanStockFinancialStatements", "A")] = make_fs(eps_per_q=1.5, ni_per_q=1e9)
    fake_finmind[("TaiwanStockBalanceSheet", "A")] = make_bs(equity=25e9, liab=10e9, capital=8e9)
    a = F.annual_fundamentals("A").loc[2025]
    assert a["eps"] == pytest.approx(6.0)
    assert a["roe"] == pytest.approx(4e9 / 25e9 * 100)       # 用歸屬母公司淨利，不是綜合損益
    assert a["bvps"] == pytest.approx(25e9 / 8e8)
    assert a["debt_ratio"] == pytest.approx(10e9 / (10e9 + 25.5e9) * 100)


def test_bank_equity_falls_back_to_total_equity(fake_finmind):
    fake_finmind[("TaiwanStockFinancialStatements", "B")] = make_fs(bank=True)
    fake_finmind[("TaiwanStockBalanceSheet", "B")] = make_bs(bank=True)
    a = F.annual_fundamentals("B").loc[2025]
    assert a["roe"] == pytest.approx(4e9 / 25.5e9 * 100)


def test_daily_system_roe_fixed_for_banks(monkeypatch):
    fs, bs = make_fs(bank=True), make_bs(bank=True)
    monkeypatch.setattr(data_mod, "fetch_finmind_cached",
                        lambda ds, *a, **k: fs.copy() if ds == "TaiwanStockFinancialStatements" else bs.copy())
    f = data_mod.get_fundamental("2886")
    assert f["roe"], "金融股以前 ROE 永遠算不出來"
    assert list(f["roe"].values())[-1] == pytest.approx(4e9 / 25.5e9 * 100, rel=1e-3)


# ── Beta ──────────────────────────────────────────────────
def test_beta_estimate():
    px, idx = make_px(beta=0.5)
    assert F.beta_at(px, idx, "2026-10-14") == pytest.approx(0.5, abs=0.1)
    assert F.beta_at(px.head(100), idx, "2026-10-14") is None   # 不足 52 週


# ── 股票池 ────────────────────────────────────────────────
def test_universe_filters_and_pool_ranking(fake_finmind):
    fake_finmind[("TaiwanStockInfo", "")] = pd.DataFrame({
        "stock_id": ["1101", "2330", "2330", "0050", "3050", "9999", "6488"],
        "stock_name": ["台泥", "台積電", "台積電", "元大台灣50", "鈺德", "某DR", "環球晶"],
        "type": ["twse", "twse", "twse", "twse", "twse", "twse", "tpex"],
        "industry_category": ["水泥工業", "半導體業", "電子工業", "ETF", "光電業", "存託憑證", "半導體業"],
        "date": "2026-10-15"})
    uni = pool_mod.candidate_universe()
    assert sorted(uni["stock_id"]) == ["1101", "2330", "3050"]      # 去重、排除 ETF/DR/上櫃
    for sid, cap in (("1101", 7e10), ("2330", 2.6e11), ("3050", 1e9)):
        fake_finmind[("TaiwanStockBalanceSheet", sid)] = pd.DataFrame(
            {"date": [pd.Timestamp("2026-06-30")], "type": ["OrdinaryShare"], "value": [cap]})
    pool = pool_mod.build_pool("2026-10-15", size=2, universe=uni, log=lambda *a: None)
    assert pool["stock_id"].tolist() == ["2330", "1101"]


# ── 篩選規則 ──────────────────────────────────────────────
def _stock(fake, sid, *, fs=None, bs=None, div=None, per=None, beta=0.5, price_base=50.0):
    fake[("TaiwanStockFinancialStatements", sid)] = fs if fs is not None else make_fs()
    fake[("TaiwanStockBalanceSheet", sid)] = bs if bs is not None else make_bs()
    fake[("TaiwanStockDividend", sid)] = div if div is not None else make_div()
    fake[("TaiwanStockPER", sid)] = per if per is not None else make_per()
    px, idx = make_px(beta=beta, base=price_base, seed=hash(sid) % 100)
    return S.StockData(sid, "2026-10-15", px=px, px_adj=px, idx=idx)


EMILY = {"min_share_capital_yi": 50, "min_bvps": 15, "min_price": 10, "min_roe": 15,
         "max_debt_ratio": 50, "cash_div_years": 2, "min_cash_div": 0,
         "min_director_holding": 20, "max_director_pledge": 33}
SUN = {"eps_avg_years": 5, "min_eps_avg": 1, "min_share_capital_yi": 300, "max_beta": 0.8,
       "cash_div_years": 5, "min_cash_div": 1, "max_pbr": 2.5}


def test_emily_rules(fake_finmind):
    # 股本 80 億、淨值 31.25、ROE 16%、負債比 28%、連年配息 → 全過
    good = _stock(fake_finmind, "G")
    c = S.evaluate_rules(good, EMILY)
    assert all(v["pass"] is not False for v in c.values()), c
    assert c["董監持股%"]["pass"] is None                     # 無資料：不擋、只標註
    # ROE 只有 8% → 只掛 ROE 一條
    low = _stock(fake_finmind, "L", fs=make_fs(ni_per_q=0.5e9))
    failed = [k for k, v in S.evaluate_rules(low, EMILY).items() if v["pass"] is False]
    assert failed == ["ROE%"]


def test_sun_rules_and_missing_data_fails_closed(fake_finmind):
    big = _stock(fake_finmind, "BIG", bs=make_bs(capital=400e8, equity=1500e8), beta=0.5)
    c = S.evaluate_rules(big, SUN)
    assert all(v["pass"] for v in c.values()), c
    # 少一年配息（2022 沒配）→ 近 5 年每年配息不過
    div = make_div(years=[2021, 2023, 2024, 2025])
    gap = _stock(fake_finmind, "GAP", bs=make_bs(capital=400e8, equity=1500e8), div=div)
    assert S.evaluate_rules(gap, SUN)["近5年每年現金股利"]["pass"] is False
    # 高 Beta
    hb = _stock(fake_finmind, "HB", bs=make_bs(capital=400e8, equity=1500e8), beta=1.3)
    assert S.evaluate_rules(hb, SUN)["Beta"]["pass"] is False
    # 估值資料過期 → PBR 視為缺 → 不通過（不可誤選）
    stale = _stock(fake_finmind, "ST", bs=make_bs(capital=400e8, equity=1500e8),
                   per=make_per(end="2026-09-01"))
    assert S.evaluate_rules(stale, SUN)["股價淨值比"]["pass"] is False


def test_screen_and_select_order_by_per(fake_finmind):
    stocks = {sid: _stock(fake_finmind, sid, per=make_per(per=p))
              for sid, p in (("A", 15.0), ("B", 9.0), ("C", -3.0), ("D", 12.0))}
    res = S.screen(stocks, {"rules": EMILY})
    assert res["stock_id"].tolist() == ["B", "D", "A", "C"]   # 虧損 PER 排最後
    assert S.select(res, 2)["stock_id"].tolist() == ["B", "D"]


def test_strategy_files_load():
    sts = {s["id"]: s for s in S.load_strategies()}
    assert set(sts) == {"dividend-emily", "dividend-sun"}
    assert sts["dividend-emily"]["rules"]["min_roe"] == 15
    assert sts["dividend-sun"]["rules"]["max_beta"] == 0.8


# ── 限流 / IP 封鎖處理 ─────────────────────────────────
def test_fetch_waits_on_rate_limit_without_rapid_retries(monkeypatch):
    calls = []

    def flaky(*a, **k):
        calls.append(k.get("max_retries"))
        if len(calls) < 3:
            raise FinMindRateLimitError("limit")
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(fetch_mod.cache_mod, "fetch_finmind_cached", flaky)
    slept = []
    df = fetch_mod.fetch("D", "1", log=lambda *a: None, sleep=slept.append)
    assert len(df) == 1 and slept == [600, 600]
    assert calls == [0, 0, 0]          # 每次只送 1 個請求，不在 cache 層連續重送


def test_fetch_waits_out_ip_ban(monkeypatch):
    import requests
    resp = requests.Response()
    resp.status_code = 403
    calls = {"n": 0}

    def banned_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.HTTPError("403", response=resp)
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(fetch_mod.cache_mod, "fetch_finmind_cached", banned_once)
    slept = []
    assert len(fetch_mod.fetch("D", "1", log=lambda *a: None, sleep=slept.append)) == 1
    assert slept == [31 * 60]


def test_batch_pacing_sets_min_interval(monkeypatch):
    from stock_strategies import cache
    monkeypatch.setattr(cache, "FINMIND_MIN_INTERVAL", 0.12)
    fetch_mod.enable_batch_pacing()
    assert cache.FINMIND_MIN_INTERVAL == pytest.approx(6.1)
    assert 3600 / cache.FINMIND_MIN_INTERVAL < 600


def test_fetch_retries_network_timeout(monkeypatch):
    import requests
    calls = {"n": 0}

    def slow(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.exceptions.ReadTimeout("Read timed out.")
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(fetch_mod.cache_mod, "fetch_finmind_cached", slow)
    slept = []
    assert len(fetch_mod.fetch("D", "2887", log=lambda *a: None, sleep=slept.append)) == 1
    assert slept == [30, 30]


def test_fetch_gives_up_after_network_retries(monkeypatch):
    import requests

    def dead(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(fetch_mod.cache_mod, "fetch_finmind_cached", dead)
    with pytest.raises(requests.ConnectionError):
        fetch_mod.fetch("D", "1", log=lambda *a: None, sleep=lambda s: None)


def test_delisted_stock_excluded_by_stale_filing(fake_finmind):
    # 2325 矽品：2018 合併下市，最後財報 2018-03-31
    fake_finmind[("TaiwanStockBalanceSheet", "2325")] = pd.DataFrame({
        "date": [pd.Timestamp("2018-03-31")], "type": ["OrdinaryShare"], "value": [3.1e10]})
    assert F.share_capital_at("2325", "2026-10-15") is None          # 今天：不在池內
    assert F.share_capital_at("2325", "2018-06-01") == 3.1e10         # 2018 年回測：當時仍在交易


def test_normal_filing_gap_still_counts(fake_finmind):
    # 4 月 1 日前只看得到前一年 9/30 季報（約 183 天前）→ 仍要算在池內
    fake_finmind[("TaiwanStockBalanceSheet", "1101")] = pd.DataFrame({
        "date": pd.to_datetime(["2025-09-30", "2025-12-31"]), "type": ["OrdinaryShare"] * 2,
        "value": [7e10, 7e10]})
    assert F.share_capital_at("1101", "2026-03-30") == 7e10


def test_quarterly_datasets_use_longer_freshness(monkeypatch):
    seen = {}

    def spy(dataset, data_id, start, **k):
        seen[dataset] = k.get("fresh_days")
        return pd.DataFrame()

    monkeypatch.setattr(fetch_mod.cache_mod, "fetch_finmind_cached", spy)
    fetch_mod.fetch("TaiwanStockBalanceSheet", "1")
    fetch_mod.fetch("TaiwanStockPrice", "1")
    assert seen == {"TaiwanStockBalanceSheet": 100, "TaiwanStockPrice": None}
