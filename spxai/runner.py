"""Command line entry point and jobs. Runs without any browser or dashboard.

  python -m spxai.runner ingest            # fetch + store bars, write health
  python -m spxai.runner backtest          # research report from stored history
  python -m spxai.runner evaluate          # one full agent cycle on the latest completed bar
  python -m spxai.runner loop              # local persistent scheduler (restart-safe)
  python -m spxai.runner status            # print the latest decision and system health
Set SPXAI_STATE_DIR to choose where state lives. Add --force to run outside market hours.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

from . import calendar as cal
from .agents import Ctx
from .alerts import AlertType, send_alert
from .backtest import report_markdown, run_research
from .tradelog import summarize, trade_log
from .config import Settings, kill_switch, load_settings, now_ny, state_dir
from .features import add_features
from .monitor import notify_signals, scan as monitor_scan
from .orchestrator import decide, run_workflow
from .paper import PaperLedger
from .providers import (CircuitBreaker, YahooProvider, data_status, get_capability, get_intraday, DataError)
from .quality import check_bars
from .store import append_jsonl, load_bars, read_json, save_bars, write_json
from .taskqueue import TaskQueue

OHLCV = ["open", "high", "low", "close", "volume"]


def default_fetch(s: Settings, now, days: int):
    return get_intraday(s, days=days, now=now)


def fetch_vix(s: Settings, now):
    try:
        return YahooProvider().intraday(s.vix_symbol, "5m", "5d", now)
    except Exception:  # noqa: BLE001  VIX is context only
        return None


# ---------------------------------------------------------------- ingest

def ingest(s: Settings, now=None, fetch=default_fetch, days: int = 60, base: Path | None = None) -> dict:
    base, now = base or state_dir(), now or now_ny()
    out = {"ts": str(now), "ok": False}
    try:
        df, src, cap = fetch(s, now, days)
        out.update(save_bars(s.symbol, "5m", df, base))
        out.update({"source": src, "fetched_rows": int(len(df)), "ok": True})
        hist = load_bars(s.symbol, "5m", base)
        q = check_bars(hist, now, max_age_sec=s.max_bar_age_sec)
        out["quality"] = q.summary()
        out["history_days"] = len({d for d in hist.index.date})
        if src != "alpaca_iex" and days > 60:
            out["warning"] = (f"Asked for {days} days but the source was {src}, which only provides about 60 days of "
                              "5-minute history. Set ALPACA_KEY_ID / ALPACA_SECRET_KEY to get years of history.")
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        send_alert(AlertType.FEED_DISCONNECTED, f"data ingest failed: {out['error']}",
                   key=f"feed-{now.strftime('%Y%m%d%H')}", base=base)
    write_json(base / "health.json", out)
    return out


# -------------------------------------------------------------- backtest

def backtest_job(s: Settings, base: Path | None = None) -> dict:
    base = base or state_dir()
    hist = load_bars(s.symbol, "5m", base)
    if len(hist) < 78 * 5:
        msg = {"status": "insufficient_history", "bars": int(len(hist)),
               "detail": "need at least about 5 trading days of stored 5-minute bars; run ingest first"}
        write_json(base / "backtest_latest.json", msg)
        return msg
    rep = run_research(add_features(hist), s)
    write_json(base / "backtest_latest.json", rep)
    (base / "reports").mkdir(exist_ok=True)
    (base / "reports" / "backtest_latest.md").write_text(report_markdown(rep))
    feats = add_features(hist)
    log = trade_log(feats, s)  # last 2 years, trade by trade
    (base / "reports").mkdir(exist_ok=True)
    log.to_csv(base / "trades_2y.csv", index=False)
    write_json(base / "trade_summary_2y.json", summarize(log, s))
    return rep


# -------------------------------------------------------------- evaluate

def evaluate(s: Settings, now=None, fetch=default_fetch, force: bool = False, base: Path | None = None,
             vix=None, chain=None) -> dict:
    base, now = base or state_dir(), now or now_ny()
    killed = kill_switch(base)
    if killed[0]:
        plan = {"decision": "NO TRADE", "timestamp": str(now), "failed_requirements": [{"requirement": "kill switch", "detail": killed[1]}]}
        write_json(base / "latest_run.json", {"plan": plan})
        return plan
    if not force and not cal.is_open(now):
        plan = {"decision": "NO TRADE", "timestamp": str(now), "failed_requirements": [{"requirement": "market open", "detail": "market is closed"}],
                "re_evaluate_when": "next regular session"}
        write_json(base / "latest_run.json", {"plan": plan})
        return plan
    breaker_state = "closed"
    try:
        new, src, cap_name = fetch(s, now, 7)
        save_bars(s.symbol, "5m", new, base)
    except Exception as e:  # noqa: BLE001
        src, cap_name, new = "none", "yahoo_finance_unofficial", None
        send_alert(AlertType.FEED_DISCONNECTED, f"live fetch failed, using stored bars only: {type(e).__name__}",
                   key=f"feed-{now.strftime('%Y%m%d%H%M')[:11]}", base=base)
        breaker_state = "open"
    hist = load_bars(s.symbol, "5m", base)
    hist = hist[hist.index + pd.Timedelta(minutes=5) <= now]
    if len(hist) < 100:
        plan = {"decision": "NO TRADE", "timestamp": str(now), "failed_requirements": [{"requirement": "history", "detail": f"only {len(hist)} stored bars"}]}
        write_json(base / "latest_run.json", {"plan": plan})
        return plan
    last_bar = str(hist.index[-1])
    ck = read_json(base / "checkpoint.json", {})
    if not force and ck.get("last_bar") == last_bar and ck.get("source") == src:
        return {"decision": "SKIPPED", "reason": "latest bar already evaluated (idempotent)", "last_bar": last_bar}
    cap = get_capability(cap_name)
    q = check_bars(hist, now, max_age_sec=s.max_bar_age_sec)
    status = data_status(cap, hist.index[-1] + pd.Timedelta(minutes=5), now, max_age_sec=s.max_bar_age_sec)
    feats = add_features(hist)
    ledger = PaperLedger(s, base)
    events = ledger.update(feats, now)
    for ev in events:
        _alert_for_event(ev, base)
    summ = ledger.summary(now)
    lock = s.account_equity * s.max_daily_loss_pct / 100
    if summ["realized_today_usd"] <= -lock:
        send_alert(AlertType.DAILY_LOCKOUT, f"daily loss limit reached (${-summ['realized_today_usd']:.0f}); no new entries today",
                   key=f"lockout-{now.date()}", base=base)
    bt = read_json(base / "backtest_latest.json")
    if bt and "strategies" not in bt:
        bt = None
    ctx = Ctx(s, now, feats, src, cap, status, q, vix=vix if vix is not None else fetch_vix(s, now), chain=chain,
              ledger=summ, backtest=bt, paper=ledger.closed(), events=cal.load_events(), kill=killed, breaker_state=breaker_state)
    run_workflow(ctx)
    plan = decide(ctx)
    try:  # live monitor: every strategy, saved for the dashboard; messages for rules that fired
        mrows = monitor_scan(s, feats)
        write_json(base / "monitor.json", {"as_of": str(now), "last_bar": last_bar, "decision": plan["decision"], "rows": mrows})
        notify_signals(mrows, plan["decision"], s, base=base, symbol=s.symbol)
    except Exception as e:  # noqa: BLE001  the monitor must never break the evaluation
        print(f"monitor failed: {type(e).__name__}: {e}")
    if plan["decision"] in ("CALL", "PUT"):
        if ledger.open_position(plan["paper_position"]):
            send_alert(AlertType.ENTRY_TRIGGER,
                       f"ENTER {'LONG (buy)' if plan['decision'] == 'CALL' else 'SHORT (sell)'} {s.symbol} via {plan['strategy_label']}\n"
                       f"Entry: next 5-minute candle open (about {plan['underlying_reference_price']})\n"
                       f"Stop (exit if wrong): {plan['stop_loss_invalidation']['underlying_stop']}\n"
                       f"Target 1: {plan['target_1']} (sell half, then move stop to entry)\n"
                       f"Target 2: {plan['target_2']} (sell the rest)\n"
                       f"Get out by {plan['time_exit']} at the latest.\n{plan['evidence_warning'] or ''}",
                       key=f"entry-{plan['paper_position']['label']}-{plan['signal_bar_start']}", base=base)
    else:
        for f in plan.get("setups_forming", []):
            send_alert(AlertType.SETUP_FORMING, f"{f['strategy']}: {f['text']}",
                       key=f"forming-{f['strategy']}-{last_bar}", base=base)
        prev = read_json(base / "latest_run.json", {}).get("plan", {})
        if prev.get("setups_forming") and not plan.get("setups_forming"):
            send_alert(AlertType.SETUP_INVALIDATED, "a previously forming setup is no longer present",
                       key=f"invalid-{last_bar}", base=base)
    full = {"plan": plan, "agents": {k: v.to_dict() for k, v in ctx.reports.items()}, "last_bar": last_bar}
    write_json(base / "latest_run.json", full)
    append_jsonl(base / "runs" / f"{now.date()}.jsonl",
                 {"ts": str(now), "last_bar": last_bar, "decision": plan["decision"], "source": src, "data_status": status,
                  "failed": [f["requirement"] for f in plan.get("failed_requirements", [])],
                  "agents": {k: v.recommendation for k, v in ctx.reports.items()},
                  "disagreements": plan.get("agent_disagreements", [])})
    write_json(base / "checkpoint.json", {"last_bar": last_bar, "source": src, "at": str(now)})
    return plan


def _alert_for_event(ev: dict, base: Path):
    t = ev["type"]
    if t == "ENTRY_FILLED":
        return  # entry alerts are sent when the setup is confirmed
    kind = {"TARGET_REACHED": AlertType.TARGET_REACHED, "EXIT_STOP": AlertType.STOP_OR_RISK_LIMIT,
            "EXIT_TIME": AlertType.MANDATORY_EXIT, "EXIT_TARGET": AlertType.TARGET_REACHED}.get(t, AlertType.SYSTEM)
    send_alert(kind, f"{t} {ev['label']}: " + json.dumps({k: v for k, v in ev.items() if k not in ('type', 'label', 'id')}),
               key=f"{t}-{ev['id']}", base=base)


# ------------------------------------------------------------- scheduler

def loop(s: Settings, base: Path | None = None, fetch=default_fetch, max_cycles: int | None = None, sleep=time.sleep):
    """Persistent local scheduler. Safe to kill and restart: tasks are durable and idempotent."""
    base = base or state_dir()
    q = TaskQueue(base)
    q.recover()
    handlers = {"ingest": lambda key: _must(ingest(s, fetch=fetch, base=base)),
                "evaluate": lambda key: evaluate(s, fetch=fetch, base=base),
                "backtest": lambda key: backtest_job(s, base)}
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        now = now_ny()
        if kill_switch(base)[0]:
            sleep(30)
            cycles += 1
            continue
        if cal.is_open(now):
            bar = now.floor("5min")
            q.enqueue("evaluate", bar.isoformat(), due=bar + pd.Timedelta(seconds=20))
        elif now.hour == 16 and now.minute < 30 and cal.is_trading_day(now.date()):
            q.enqueue("ingest", str(now.date()))
            q.enqueue("backtest", str(now.date()))
        while q.run_one(handlers) is not None:
            pass
        nxt = now.ceil("5min") + pd.Timedelta(seconds=20)
        sleep(max(1.0, (nxt - now_ny()).total_seconds()))
        cycles += 1


def _must(res: dict):
    if not res.get("ok"):
        raise DataError(res.get("error", "ingest failed"))


# ------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spxai")
    ap.add_argument("cmd", choices=["ingest", "backtest", "evaluate", "loop", "status", "report", "telegram-test"])
    ap.add_argument("--force", action="store_true", help="run even if the market is closed")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args(argv)
    s, base = load_settings(), state_dir()
    try:
        if a.cmd == "ingest":
            print(json.dumps(ingest(s, days=a.days), indent=2, default=str))
        elif a.cmd == "backtest":
            r = backtest_job(s)
            print((base / "reports" / "backtest_latest.md").read_text() if "strategies" in r else json.dumps(r, indent=2))
        elif a.cmd == "evaluate":
            print(json.dumps(evaluate(s, force=a.force), indent=2, default=str))
        elif a.cmd == "telegram-test":
            from .alerts import find_chat_ids, send_telegram, telegram_enabled
            if os.environ.get("TELEGRAM_BOT_TOKEN") and not os.environ.get("TELEGRAM_CHAT_ID"):
                ids, st = find_chat_ids()
                if ids:
                    print("FOUND YOUR CHAT ID: " + ", ".join(str(i) for i in ids))
                    print("Copy that number into a new repo secret named TELEGRAM_CHAT_ID, then run this test again.")
                    return 0
                msg = {"bad_token": "Telegram says the token is wrong. Check the TELEGRAM_BOT_TOKEN secret: the whole token, with the colon, no spaces.",
                       "no_messages": "The token works, but your bot has no messages yet. Open your bot in Telegram, send it the word hello, then run this test again."}
                print("CHAT ID NOT FOUND (" + st + "). " + msg.get(st, "Try again in a minute."))
                return 1
            if not telegram_enabled():
                print("TELEGRAM NOT CONFIGURED: add the secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to the repo.")
                return 1
            res = send_telegram("Test message from your SPY practice system. If you can read this, Telegram alerts work. Nothing was traded.")
            print("Telegram result:", res)
            return 0 if res == "delivered" else 1
        elif a.cmd == "loop":
            loop(s)
        elif a.cmd in ("status", "report"):
            print(json.dumps(read_json(base / "latest_run.json", {}).get("plan", {}), indent=2, default=str))
            print(json.dumps(read_json(base / "health.json", {}), indent=2, default=str))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
