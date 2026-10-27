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

## Where to get out, and chart reading
Every signal gives a stop, two targets and the 15:45 ET time exit. There are two ways to set the stop and targets (`exit_mode` in `config/settings.yaml`):
- `atr` (default): the stop is a fixed multiple of the recent average candle range, the targets are fixed multiples of the risk.
- `chart`: the stop goes just beyond the nearest support (long) or resistance (short) zone and the targets just before the next opposing zones. If there is no sensible
  chart plan (no room before the next zone, or the stop would be too wide) the practice trade is passed and the reason is shown.
Alerts always show the chart-based alternative too. If the 15-minute trend structure breaks while a practice trade is open you get a "consider getting out" warning
(advice only, it does not close the paper trade). The Reliability test tab compares the two exit styles on identical trades; keep `atr` until it shows an advantage.
A research-only strategy, `zone_rejection`, tests the trader-style checklist (rejection candle at a STRONG zone, bigger trend agrees, 2R of room, stop behind the zone, long and short). It never trades or alerts unless it first passes the fair test and is moved to the live list.
The **Levels & Trends** tab shows support/resistance zones and the trend on four time scales, and the Spreads tab now places sold strikes beyond strong zones.

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
