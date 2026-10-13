# SPY multi-agent research and paper-trading platform ($0)

**Paper trading only. Nothing here places real orders. No profit is promised; "NO TRADE" is a normal answer.**
SPY underlying, 5-minute entries with 1-hour context. Real-time option quotes are not available for free, so trades are simulated SPY shares.

## Deploy free on GitHub (step by step)
1. **Alpaca keys (free):** sign up at alpaca.markets, choose Paper trading, generate an API key and secret. Never paste them in chat or commit them.
2. **GitHub repo:** create a **public** repo (public = unlimited free Actions minutes; private gives ~2,000 min/month and a 5-minute cadence uses ~1,600+). Upload all files, including the hidden `.github` folder.
3. **Secrets:** repo Settings > Secrets and variables > Actions > New secret: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` (optional `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
4. **Enable Actions** (Actions tab). Run **"Daily ingest and backtest"** manually (optionally `days` = 60). Then run **"Evaluate"** once. After that it runs by itself during market hours.
5. **Dashboard:** share.streamlit.io > New app > your repo, main file `dashboard/app.py`. In app Secrets add
   `SPXAI_STATE_URL = "https://raw.githubusercontent.com/<you>/<repo>/data"` (private repo: also a `GITHUB_TOKEN`).
   State lives on an auto-created `data` branch.

## Kill switch
Create a file named `KILL_SWITCH` in the state folder, or set env `SPXAI_KILL=1`, or `control.json`. All new setups stop.

## Run locally (Windows)
```
py -3.13 -m pip install -r requirements.txt
py -3.13 -m spxai.runner ingest --days 30
py -3.13 -m spxai.runner backtest
py -3.13 -m spxai.runner evaluate --force
py -3.13 -m streamlit run dashboard/app.py
```
Tests: `py -3.13 -m pip install -r requirements-dev.txt` then `py -3.13 -m pytest`.

## Known limits
- GitHub cron: 5-minute minimum, can be delayed, UTC only. Streamlit Cloud apps sleep after ~12 h idle.
- Yahoo fallback is unofficial and shown as "delayed", never "live". Alpaca free = IEX-only volume.
- Free intraday history is short, so the daily job accumulates it. Expect INSUFFICIENT_SAMPLE for a while.
- See `docs/DATA_SOURCES.md` and `docs/STATUS.md`.
