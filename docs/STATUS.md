# Status: completed / incomplete / blocked

## Completed (verified by simulated-data tests: 72 pass, 1 live-network test skipped)
- $0 data policy with provider registry, cost guard and exact "options unavailable" message
- Data layer: Alpaca IEX (primary), Yahoo (fallback, labelled delayed/unverified), quality checks, safe store, NYSE calendar
- 5-minute entries with 1-hour context (hourly bar used only after the hour completes); no-look-ahead tests
- Five strategies plus a baseline, versioned with parameter hashes
- Backtester: next-bar-open fills, slippage, stop-wins-ambiguity, chronological train/val/OOS, walk-forward, ablation, sensitivity, bootstrap CIs, verdicts
- 11 deterministic agents with structured reports, timeout and fail-closed behaviour; orchestrator decision engine
- Independent risk manager, kill switch, daily loss lockout, no martingale
- Paper ledger (shared exit logic with backtester), alerts (Telegram optional), durable task queue
- Streamlit dashboard (5m + 1h charts), GitHub Actions workflows, state branch scripts

## Incomplete / unverified
- AlpacaProvider, Alpaca options adapter, dashboard and GitHub workflows have NOT been run against live services (no network/keys in build sandbox). Expect first-run fixes.
- Tested on Python 3.13 / pandas 3 only.
- FOMC dates in config/events.yaml are unverified; holiday table covers 2026-2027 only.
- Agents are rule-based, not LLM-driven.
- Scheduler = best-effort GitHub cron (5 min minimum, may be delayed) plus a local `loop` runner.

## Blocked by the $0 requirement
- Real-time executable SPY option quotes, historical option quotes: so option contract selection and options profitability research stay UNAVAILABLE. Paper trades are simulated SPY shares.
- Full-market volume: free feed is IEX only (a small share of consolidated volume).

## Intentionally not built
- Live broker order placement.
- No profitability claim: until 100+ out-of-sample trades exist, verdicts read INSUFFICIENT_SAMPLE.
