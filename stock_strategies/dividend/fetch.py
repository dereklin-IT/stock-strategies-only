"""定存股模組的取數：長歷史 + 主動控速 + 不連續撞限流（避免 IP 被封鎖）。

FinMind 規定（官方文件「方案與價格」「IP 封鎖政策」）：
- 免費註冊會員 600 次／小時；超過回 402
- 短時間大量 4xx（含超額後持續重送）→ IP 封鎖 30 分鐘，所有請求回 403 "ip banned"
  （公司網路可能多人共用同一個對外 IP，被封會影響同事）

因此批次作業：
1. 主動控速：每次實際打 API 至少間隔 BATCH_MIN_INTERVAL 秒（6.1 秒 ≈ 590 次／小時），
   正常情況永遠不會收到 402。快取命中不打 API、不等待。
2. 若仍收到 402（例如同一小時內每日系統也在跑），只送「1 次」就停，等 10 分鐘再試，
   不做快速連續重試（cache 層的 max_retries 設為 0）。
3. 若收到 403 ip banned，等 31 分鐘（封鎖 30 分鐘自動解除）再試。
4. 網路逾時 / 連線中斷（暫時性，非 4xx，不會觸發封鎖）：等 30 秒重試，最多 3 次。
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from .. import cache as cache_mod
from ..cache import FinMindRateLimitError

HIST_START = "2010-01-01"        # 回測需要的最早資料（2015 起回測 + 5 年回看）
BATCH_MIN_INTERVAL = 6.1         # 秒；3600 / 6.1 ≈ 590 次／小時 < 免費上限 600
RATE_LIMIT_WAIT_SEC = 600        # 402 後等待
RATE_LIMIT_MAX_WAITS = 12        # 最多等 2 小時
IP_BAN_WAIT_SEC = 31 * 60        # 403 ip banned 後等待
IP_BAN_MAX_WAITS = 2
# 季報類資料的快取新鮮天數（工作日）。預設 60 天會在季報空窗期（例如 6/30 季報後到 11/14 前）
# 把整池上千檔判為過期而重抓；100 個工作日內頂多晚一季拿到新財報，回測/年度篩選足夠。
QUARTERLY_FRESH_DAYS = 100
_QUARTERLY = {"TaiwanStockBalanceSheet", "TaiwanStockFinancialStatements"}
NETWORK_RETRY_WAIT_SEC = 30
NETWORK_MAX_RETRIES = 3
_NETWORK_ERRORS = (requests.Timeout, requests.ConnectionError,
                   requests.exceptions.ChunkedEncodingError)


def enable_batch_pacing(interval: float = BATCH_MIN_INTERVAL) -> None:
    """批次模式：拉長 cache 層相鄰 API 請求的最小間隔。"""
    cache_mod.FINMIND_MIN_INTERVAL = interval


def _is_ip_banned(e: Exception) -> bool:
    resp = getattr(e, "response", None)
    return resp is not None and resp.status_code == 403


def fetch(dataset: str, data_id: str, start: str = HIST_START, *, log=print,
          sleep=time.sleep) -> pd.DataFrame:
    limit_waits = ban_waits = net_retries = 0
    while True:
        try:
            # max_retries=0：撞限流只送 1 次就交回這裡處理，不在短時間內連續重送
            return cache_mod.fetch_finmind_cached(
                dataset, data_id, start, max_retries=0,
                fresh_days=QUARTERLY_FRESH_DAYS if dataset in _QUARTERLY else None)
        except FinMindRateLimitError:
            if limit_waits >= RATE_LIMIT_MAX_WAITS:
                raise
            limit_waits += 1
            log(f"  ⏳ FinMind 本小時額度已滿（402），等 {RATE_LIMIT_WAIT_SEC // 60} 分鐘"
                f"（{limit_waits}/{RATE_LIMIT_MAX_WAITS}）…")
            sleep(RATE_LIMIT_WAIT_SEC)
        except _NETWORK_ERRORS as e:
            if net_retries >= NETWORK_MAX_RETRIES:
                raise
            net_retries += 1
            log(f"  🌐 {dataset} {data_id} 網路逾時/中斷（{type(e).__name__}），"
                f"{NETWORK_RETRY_WAIT_SEC} 秒後重試（{net_retries}/{NETWORK_MAX_RETRIES}）…")
            sleep(NETWORK_RETRY_WAIT_SEC)
        except requests.HTTPError as e:
            if not _is_ip_banned(e) or ban_waits >= IP_BAN_MAX_WAITS:
                raise
            ban_waits += 1
            log(f"  🚫 IP 暫時被 FinMind 封鎖（403），等 {IP_BAN_WAIT_SEC // 60} 分鐘自動解除…")
            sleep(IP_BAN_WAIT_SEC)
