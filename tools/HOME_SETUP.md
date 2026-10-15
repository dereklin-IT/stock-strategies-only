# 家用電腦設定：每日選股 + 夜盤快報（本機排程）

> **2026-10-15 更新：已改用 Teams 推播（公司網路可送），每日選股與夜盤快報可直接在公司電腦跑，本文件暫不需要。**
> 只有在想改回 Telegram、或要在家用電腦跑時才照下面做；那時在 `.env` 設 `NOTIFY_CHANNEL=telegram`（或 `both`）。

公司網路擋 Telegram，所以每日選股（14:30）與夜盤快報（08:00）改在家用電腦跑。
定存股批次（`dividend.py`）、回測這類耗時工作，仍在公司電腦跑即可。

## 1. 從公司電腦複製資料夾（用隨身碟）

在公司電腦的 PowerShell 執行（`E:` 換成你的隨身碟代號）：

```powershell
robocopy "D:\Users\64270\Documents\Claude\stock-strategies-only-main" "E:\stock-strategies-only-main" /E /XD .venv logs _review __pycache__ node_modules
```

- `.env` 會一起複製（裡面有 token），**不要**放到公開的雲端資料夾。
- `.venv` 不要複製，家裡會重建。
- `.cache` 會一起複製，可省 FinMind 額度。

## 2. 家用電腦安裝 uv（只需一次）

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

裝完**關掉 PowerShell 重開**。

> 公司電腦設過的憑證環境變數（`REQUESTS_CA_BUNDLE`、`SSL_CERT_FILE`、`UV_SYSTEM_CERTS`）是公司網路專用，家裡**不需要**設定。

## 3. 安裝套件並測試

把資料夾放到家用電腦（例如 `D:\stock-strategies-only-main`），在該資料夾開 PowerShell：

```powershell
uv sync
uv run pytest -q
```

最後一行應為 `200 passed`。

## 4. 手動跑一次

```powershell
.\tools\run_job.cmd main
Get-Content (Get-ChildItem logs\main_*.log | Sort-Object LastWriteTime | Select-Object -Last 1) -Encoding UTF8 -Tail 25
```

- Telegram 應收到選股報告。
- log 最後應為 `✅ 完成（Telegram N 則）` 與 `exit=0`。
- 若出現 `❌ Telegram 送失敗` 與 `exit=2`：看那一行的原因（token 錯、chat not found 等）。

夜盤快報也測一次：

```powershell
.\tools\run_job.cmd premarket
```

## 5. 安裝排程

```powershell
.\tools\install_schedule.cmd
```

會建立兩個工作（週一～五）：
- `StockStrategies\Premarket` 08:00 夜盤快報
- `StockStrategies\Daily` 14:30 每日選股

並設定：電腦睡眠時會**自動喚醒**執行；錯過時間（例如當時關機）會在開機後補跑。
移除排程：`.\tools\install_schedule.cmd remove`

## 6. 電源設定（讓睡眠中的電腦能被喚醒）

控制台 → 電源選項 → 變更計劃設定 → 變更進階電源設定 → 睡眠 → **允許喚醒計時器 → 啟用**。

- 電腦可以「睡眠」，但不能「關機」（關機無法喚醒，只能等開機後補跑）。
- 需要保持 Windows 帳號登入狀態（鎖定畫面沒關係）。

## 7. 停用 GitHub 上的排程（避免重複推播、共用 FinMind 額度）

GitHub → dereklin-IT/stock-strategies-only → Actions：
- **V3 Daily Signal** → 右上「…」→ Disable workflow
- **Premarket Night Session** → 同上

## 之後程式有更新時

公司電腦改好的程式，用同一個 robocopy 指令再複製一次覆蓋即可（`.env`、`.cache` 也會更新）。
之後改用 git 同步後就不用手動複製。
