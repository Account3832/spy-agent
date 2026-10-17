"""Trade-by-trade log and win/loss breakdown for the most recent N days (default 2 years).

This replays every strategy over stored 5-minute bars using the SAME simulator as the research report
(next-bar-open entry, slippage, stop wins ambiguous bars, mandatory time exit). It describes what the
fixed default rules would have done; it is NOT an out-of-sample test and not a prediction.
A trade is a WIN when its net result after costs is above zero, a LOSS when below zero.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .backtest import metrics, simulate
from .config import Settings
from .strategies import BASELINE, REGISTRY

COLUMNS = ["strategy", "date", "signal_time", "entry_time", "exit_time", "direction", "entry", "risk_per_share",
           "gross_R", "net_R", "result", "pnl_usd_illustrative", "exit_reason", "regime", "tod_bucket", "trigger",
           "minutes_held", "month"]


def _wl(r: float) -> str:
    return "WIN" if r > 0 else ("LOSS" if r < 0 else "FLAT")


def trade_log(d: pd.DataFrame, s: Settings, days: int = 730, names: list[str] | None = None) -> pd.DataFrame:
    """All simulated trades from the last `days` calendar days of `d` (features already added)."""
    if d is None or len(d) == 0:
        return pd.DataFrame(columns=COLUMNS)
    start = pd.Timestamp(d.index[-1]).date() - dt.timedelta(days=days)
    budget = s.account_equity * s.max_risk_pct / 100.0
    strategies = [REGISTRY[n]() for n in (names or s.enabled_strategies)] + [BASELINE()]
    parts = []
    for strat in strategies:
        t = simulate(d, strat, s)
        if len(t) == 0:
            continue
        t = t[t.date >= start].copy()
        if len(t) == 0:
            continue
        t["strategy"] = strat.name
        t["direction"] = t.dir.map({1: "LONG", -1: "SHORT"})
        t["risk_per_share"] = t.risk.round(4)
        t["entry"] = t.entry.round(2)
        t["net_R"] = t.net_R.round(3)
        t["gross_R"] = t.gross_R.round(3)
        t["result"] = t.net_R.map(_wl)
        t["pnl_usd_illustrative"] = (t.net_R * budget).round(2)
        t["minutes_held"] = ((pd.to_datetime(t.exit_time, utc=True) - pd.to_datetime(t.entry_time, utc=True))
                             .dt.total_seconds() / 60 + 5).round(0).astype(int)
        t["month"] = pd.to_datetime(t.date).dt.strftime("%Y-%m")
        parts.append(t[COLUMNS])
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(parts, ignore_index=True).sort_values(["strategy", "entry_time"]).reset_index(drop=True)
    for c in ("date", "signal_time", "entry_time", "exit_time"):
        out[c] = out[c].astype(str)
    return out


def _counts(g: pd.DataFrame) -> dict:
    n = len(g)
    w, l = int((g.result == "WIN").sum()), int((g.result == "LOSS").sum())
    r = g.net_R.astype(float)
    return {"trades": n, "wins": w, "losses": l, "flat": n - w - l,
            "win_rate": round(w / n, 3) if n else None,
            "avg_win_R": round(float(r[r > 0].mean()), 3) if w else 0.0,
            "avg_loss_R": round(float(r[r < 0].mean()), 3) if l else 0.0,
            "total_net_R": round(float(r.sum()), 2), "net_exp_R": round(float(r.mean()), 3) if n else None,
            "total_pnl_usd_illustrative": round(float(g.pnl_usd_illustrative.sum()), 2)}


def summarize(t: pd.DataFrame, s: Settings, days: int = 730) -> dict:
    out = {"window_days": days, "win_definition": "net result after costs above zero",
           "risk_per_trade_usd_illustrative": round(s.account_equity * s.max_risk_pct / 100.0, 2),
           "notes": ["Fixed-risk illustration (no compounding). Not an out-of-sample test and not a prediction.",
                     "The default rules were replayed on all of this window; the research report's out-of-sample result is the fairer test.",
                     "Underlying SPY shares only; option profit is not modelled."],
           "strategies": {}}
    if len(t) == 0:
        return out
    out["window"] = {"first": str(t.date.min()), "last": str(t.date.max())}
    for name, g in t.groupby("strategy"):
        m = metrics(g)
        entry = {**_counts(g), "max_drawdown_R": m.get("max_drawdown_R"), "longest_losing_streak": m.get("longest_losing_streak"),
                 "profit_factor": m.get("profit_factor"), "by": {}}
        for col in ("direction", "regime", "tod_bucket", "trigger", "month"):
            entry["by"][col] = {str(k): _counts(x) for k, x in g.groupby(col)}
        out["strategies"][name] = entry
    return out
