"""Backtesting engine for SPY SHARE-price signals (UNDERLYING research only).

This module does NOT measure option profitability. The options report is always
marked UNAVAILABLE unless real historical option quotes are supplied (none are free).

Rules that prevent flattering results:
  * signal at bar close, entry at the NEXT bar's open, plus slippage
  * ambiguous bars (stop and target both reachable) resolve to the STOP
  * targets need a trade-through of the slippage buffer to count
  * T2 can only fill on a bar after T1, and the stop moves to breakeven only for later bars
  * parameters are fixed a priori; the grid and ablations use train+validation only
  * the final out-of-sample block is reported once and never used to select anything
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import numpy as np
import pandas as pd

from . import calendar as cal
from .config import Settings
from .strategies import ALL_FILTERS, BASELINE, REGISTRY, Strategy

TOD_BUCKETS = [("09:45", "10:30"), ("10:30", "12:00"), ("12:00", "14:00"), ("14:00", "15:00"), ("15:00", "16:00")]


def _bucket(tod: str) -> str:
    for a, b in TOD_BUCKETS:
        if a <= tod < b:
            return f"{a}-{b}"
    return "other"


def resolve_trade(o, h, l, c, tod, date, i0, k, e_ref, entry, risk, stop, t1, t2, slip, exit_tod_day,
                  max_hold, data_end_closes=True) -> dict:
    """Walk bars from the entry bar i0 and resolve one 2-half position (T1 half, T2 half).

    Shared by the backtester and the paper ledger so both use identical, conservative rules.
    Returns price differences (already multiplied by direction) averaged over the two halves.
    `closed` is False only when the bars ran out first and data_end_closes is False (still open)."""
    n, dte = len(o), date[i0]
    buf = slip
    stop_cur, t1_bar = stop, None
    open_halves = {1: True, 2: True}
    acc = {"gross": 0.0, "net": 0.0}
    reasons, last_j = {}, i0

    def close_half(which, ref_px, fill_px, why):
        open_halves[which] = False
        acc["gross"] += 0.5 * (ref_px - e_ref) * k
        acc["net"] += 0.5 * (fill_px - entry) * k
        reasons[which] = why

    j = i0
    while j < n and date[j] == dte and any(open_halves.values()):
        last_j = j
        held = j - i0
        if tod[j] >= exit_tod_day or held >= max_hold:
            why = "time" if tod[j] >= exit_tod_day else "maxhold"
            for w in (1, 2):
                if open_halves[w]:
                    close_half(w, o[j], o[j] - k * slip, why)
            break
        hit_stop = (l[j] <= stop_cur) if k == 1 else (h[j] >= stop_cur)
        if hit_stop:  # conservative: the stop wins any ambiguous bar
            lvl = min(stop_cur, o[j]) if k == 1 else max(stop_cur, o[j])  # gaps through the stop fill at the open
            be = stop_cur == entry
            for w in (1, 2):
                if open_halves[w]:
                    close_half(w, lvl, lvl - k * slip, "BE-stop" if be else "stop")
            break
        if open_halves[1] and ((h[j] >= t1 + buf) if k == 1 else (l[j] <= t1 - buf)):
            close_half(1, t1, t1, "T1")
            stop_cur, t1_bar = entry, j  # breakeven applies from the NEXT bar
        elif (not open_halves[1]) and open_halves[2] and t1_bar is not None and j > t1_bar \
                and ((h[j] >= t2 + buf) if k == 1 else (l[j] <= t2 - buf)):
            close_half(2, t2, t2, "T2")
        j += 1
    still_open = any(open_halves.values())
    if still_open and data_end_closes:
        for w in (1, 2):
            if open_halves[w]:
                close_half(w, c[last_j], c[last_j] - k * slip, "data_end")
        still_open = False
    return {"closed": not still_open, "gross": acc["gross"], "net": acc["net"], "reasons": reasons,
            "last_j": last_j, "halves_open": dict(open_halves)}


# ------------------------------------------------------------- simulation

def simulate(d: pd.DataFrame, strat: Strategy, s: Settings, dates: set | None = None) -> pd.DataFrame:
    sig = strat.signals(d, s.entry_start, s.entry_end)
    dirs = sig["dir"].values
    n = len(d)
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    atr = d.atr.values
    tod, date = d.tod.values, d.date.values
    idx = d.index
    slip, comm = s.slippage_usd, s.commission_usd
    exit_tod: dict = {}
    trades, busy_until, per_day = [], -1, defaultdict(int)

    for i in np.flatnonzero(dirs != 0):
        if i <= busy_until or i + 1 >= n or date[i + 1] != date[i]:
            continue
        dte = date[i]
        if dates is not None and dte not in dates:
            continue
        if per_day[dte] >= s.max_trades_per_day or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if dte not in exit_tod:
            exit_tod[dte] = cal.mandatory_exit_ts(dte, s.mandatory_exit).strftime("%H:%M")
        if tod[i + 1] >= exit_tod[dte]:
            continue
        k = int(dirs[i])
        e_ref = o[i + 1]
        entry = e_ref + k * slip
        risk = strat.stop_atr * atr[i]
        stop, t1, t2 = entry - k * risk, entry + k * strat.t1_r * risk, entry + k * strat.t2_r * risk
        res = resolve_trade(o, h, l, c, tod, date, i + 1, k, e_ref, entry, risk, stop, t1, t2, slip,
                            exit_tod[dte], strat.max_hold_bars, data_end_closes=True)
        gross, net, reasons, last_j = res["gross"], res["net"] - 2 * comm, res["reasons"], res["last_j"]
        trades.append({
            "strategy": strat.name, "label": strat.label(), "date": dte, "signal_time": idx[i],
            "entry_time": idx[i + 1], "exit_time": idx[last_j], "dir": k, "entry": entry, "risk": risk,
            "gross_R": gross / risk, "net_R": net / risk,
            "exit_reason": f"{reasons.get(1, '')}|{reasons.get(2, '')}", "regime": d.regime.values[i],
            "tod_bucket": _bucket(tod[i]), "trigger": sig["trigger"].values[i]})
        busy_until = last_j
        per_day[dte] += 1
    cols = ["strategy", "label", "date", "signal_time", "entry_time", "exit_time", "dir", "entry", "risk",
            "gross_R", "net_R", "exit_reason", "regime", "tod_bucket", "trigger"]
    return pd.DataFrame(trades, columns=cols)


# ---------------------------------------------------------------- metrics

def boot_ci(r: np.ndarray, level: float = 0.95, n_boot: int = 2000, seed: int = 7):
    """Percentile bootstrap CI of the mean. Ignores serial correlation between trades; treat as optimistic."""
    if len(r) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n_boot, len(r)), replace=True).mean(axis=1)
    a = (1 - level) / 2
    return (float(np.quantile(means, a)), float(np.quantile(means, 1 - a)))


def metrics(t: pd.DataFrame, level: float = 0.95) -> dict:
    if t is None or len(t) == 0:
        return {"trades": 0}
    r = t.net_R.values.astype(float)
    g = t.gross_R.values.astype(float)
    wins, losses = r[r > 0], r[r <= 0]
    cum = np.cumsum(r)
    dd = float((np.maximum.accumulate(cum) - cum).max())
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    lo, hi = boot_ci(r, level)
    sd = r.std(ddof=1) if len(r) > 1 else float("nan")
    return {"trades": int(len(r)), "win_rate": round(float((r > 0).mean()), 3),
            "avg_win_R": round(float(wins.mean()), 3) if len(wins) else 0.0,
            "avg_loss_R": round(float(losses.mean()), 3) if len(losses) else 0.0,
            "gross_expectancy_R": round(float(g.mean()), 3), "net_expectancy_R": round(float(r.mean()), 3),
            "total_net_R": round(float(r.sum()), 2),
            "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if losses.sum() < 0 else None,
            "max_drawdown_R": round(dd, 2), "longest_losing_streak": int(streak),
            "t_stat": round(float(r.mean() / (sd / np.sqrt(len(r)))), 2) if sd and sd > 0 else None,
            "ci_level": round(level, 4), "ci_low": round(lo, 3), "ci_high": round(hi, 3)}


def by(t: pd.DataFrame, col: str) -> dict:
    return {str(k): {m: v for m, v in metrics(g).items() if m in ("trades", "net_expectancy_R", "win_rate", "total_net_R")}
            for k, g in t.groupby(col)} if len(t) else {}


# ------------------------------------------------------------------ splits

def chrono_split(dates: list, frac=(0.5, 0.25, 0.25)) -> dict:
    n = len(dates)
    a, b = int(n * frac[0]), int(n * (frac[0] + frac[1]))
    return {"train": dates[:a], "val": dates[a:b], "oos": dates[b:]}


def walk_forward_folds(dates: list, test_days: int = 10, min_train: int = 20) -> list[dict]:
    out = []
    for s0 in range(min_train, len(dates), test_days):
        test = dates[s0:s0 + test_days]
        if len(test) >= max(3, test_days // 2):
            out.append({"train_end": str(dates[s0 - 1]), "test_start": str(test[0]), "test_end": str(test[-1]),
                        "test_dates": test})
    return out


def _slice(t: pd.DataFrame, dates) -> pd.DataFrame:
    ds = set(dates)
    return t[t["date"].isin(ds)] if len(t) else t


# ----------------------------------------------------------------- verdict

def edge_verdict(oos: dict, folds_pos_frac: float | None, base_oos_exp: float | None, min_trades: int) -> tuple[str, str]:
    n = oos.get("trades", 0)
    if n < min_trades:
        return "INSUFFICIENT_SAMPLE", f"only {n} out-of-sample trades; at least {min_trades} are required before any claim"
    exp, lo, hi = oos["net_expectancy_R"], oos["ci_low"], oos["ci_high"]
    if exp <= 0 and hi < 0:
        return "FAILED_OOS", f"out-of-sample net expectancy {exp}R and the CI upper bound {hi}R is below zero"
    if exp > 0 and lo > 0 and (folds_pos_frac or 0) >= 0.6 and (base_oos_exp is None or exp > base_oos_exp):
        return "UNDERLYING_EDGE_SUPPORTED", ("positive net expectancy with a CI above zero, positive in most folds, beats baseline. "
                                             "This is underlying SPY share evidence ONLY, not option profitability")
    return "NOT_DEMONSTRATED", f"out-of-sample net expectancy {exp}R, CI [{lo}, {hi}]; no reliable edge shown"


# ---------------------------------------------------------------- research

GRID_STOP = (1.0, 1.5, 2.0)
GRID_T1 = (1.0, 1.5, 2.0)


def run_research(d: pd.DataFrame, s: Settings, strategy_names=None, grid: bool = True) -> dict:
    names = list(strategy_names or s.enabled_strategies)
    dates = sorted(set(d.date))
    sp = chrono_split(dates)
    tv_dates = sp["train"] + sp["val"]
    level = 1 - 0.05 / max(1, len(names))  # Bonferroni across strategies
    warnings = []
    if len(dates) < 60:
        warnings.append(f"Only {len(dates)} trading days of 5-minute data. Free providers give limited intraday history; "
                        "daily ingestion grows this over time. Splits and CIs are very noisy at this size.")
    base_t = simulate(d, BASELINE(), s)
    base_oos = metrics(_slice(base_t, sp["oos"]), level)
    folds = walk_forward_folds(dates)
    n_cfg, results = 0, {}
    for name in names:
        cls = REGISTRY[name]
        strat = cls()
        t = simulate(d, strat, s)
        n_cfg += 1
        oos = metrics(_slice(t, sp["oos"]), level)
        fold_res = []
        for f in folds:
            m = metrics(_slice(t, f["test_dates"]), level)
            fold_res.append({"test": f"{f['test_start']}..{f['test_end']}", "trades": m["trades"],
                             "net_expectancy_R": m.get("net_expectancy_R")})
        scored = [f for f in fold_res if f["trades"] > 0]
        pos_frac = (sum(1 for f in scored if f["net_expectancy_R"] > 0) / len(scored)) if scored else None
        entry = {"label": strat.label(), "spec": {k: v for k, v in strat.spec.__dict__.items() if k != "params"},
                 "all_sample_in_sample_reference": metrics(t, level),
                 "train": metrics(_slice(t, sp["train"]), level), "validation": metrics(_slice(t, sp["val"]), level),
                 "out_of_sample_reported_once": oos, "walk_forward_folds": fold_res, "folds_positive_fraction": pos_frac,
                 "by_regime_descriptive": by(t, "regime"), "by_time_of_day_descriptive": by(t, "tod_bucket")}
        if len(t):  # single-session dependence
            by_day = t.groupby("date").net_R.sum()
            tot = by_day.sum()
            entry["top_day_share_of_total_R"] = round(float(by_day.max() / tot), 2) if tot > 0 else None
        if grid:  # sensitivity on train+val ONLY
            cells = {}
            for sa in GRID_STOP:
                for t1 in GRID_T1:
                    st = cls(stop_atr=sa, t1_r=t1, t2_r=2 * t1)
                    m = metrics(_slice(simulate(d, st, s), tv_dates), level)
                    cells[f"stop{sa}_T1{t1}"] = {"trades": m["trades"], "net_expectancy_R": m.get("net_expectancy_R")}
                    n_cfg += 1
            ev = [v["net_expectancy_R"] for v in cells.values() if v["trades"] > 0]
            entry["sensitivity_train_val"] = {"cells": cells,
                                              "fraction_positive": round(sum(1 for x in ev if x > 0) / len(ev), 2) if ev else None}
            abl = {}
            base_m = metrics(_slice(t, tv_dates), level)
            abl["with_default_filters"] = {"trades": base_m["trades"], "net_expectancy_R": base_m.get("net_expectancy_R")}
            for f in ALL_FILTERS:
                on = set(strat.filters)
                variant = tuple(sorted(on - {f})) if f in on else tuple(sorted(on | {f}))
                m = metrics(_slice(simulate(d, cls(filters=variant), s), tv_dates), level)
                abl[("drop_" if f in on else "add_") + f] = {"trades": m["trades"], "net_expectancy_R": m.get("net_expectancy_R")}
                n_cfg += 1
            entry["ablation_train_val"] = abl
        verdict, why = edge_verdict(oos, pos_frac, base_oos.get("net_expectancy_R"), s.min_trades_for_claim)
        entry["verdict"], entry["verdict_reason"] = verdict, why
        results[name] = entry
    return {
        "report_type": "UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.",
        "generated_at": str(pd.Timestamp.now(tz="America/New_York")),
        "data": {"symbol": s.symbol, "first": str(d.index[0]), "last": str(d.index[-1]), "bars": int(len(d)),
                 "trading_days": len(dates), "splits_days": {k: len(v) for k, v in sp.items()}},
        "assumptions": {"slippage_usd_per_share_per_side": s.slippage_usd, "commission_usd": s.commission_usd,
                        "entry": "next bar open + slippage", "ambiguous_bars": "stop first",
                        "target_fill": "requires trade-through by the slippage buffer",
                        "mandatory_exit": s.mandatory_exit, "max_trades_per_day": s.max_trades_per_day},
        "baseline_hourly_trend": {"out_of_sample": base_oos, "all_sample": metrics(base_t, level)},
        "strategies": results,
        "multiple_testing": {"configurations_evaluated": n_cfg, "ci_level_used": round(level, 4),
                             "note": "Bonferroni-adjusted across strategies. Grids and ablations used train+validation only."},
        "options_profitability_research": {
            "status": "UNAVAILABLE",
            "reason": "No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, "
                      "not actual quotes, and cannot support execution-realistic option backtests.",
            "consequence": "Nothing in this report shows that option trades would have been profitable."},
        "warnings": warnings,
        "limitations": ["Bootstrap CIs ignore serial correlation between trades.",
                        "Free intraday history is short; regime coverage may be poor.",
                        "IEX-only volume understates real volume; VWAP/relative volume are approximations.",
                        "Past results do not predict future results."]}


def report_markdown(rep: dict) -> str:
    L = [f"# Backtest report", f"**{rep['report_type']}**", "",
         f"Data: {rep['data']['symbol']} {rep['data']['first']} to {rep['data']['last']} "
         f"({rep['data']['trading_days']} trading days). Generated {rep['generated_at']}.", ""]
    for w in rep["warnings"]:
        L.append(f"> WARNING: {w}")
    L += ["", "| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |", "|---|---|---|---|---|"]
    for n, r in rep["strategies"].items():
        o = r["out_of_sample_reported_once"]
        ci = f"[{o.get('ci_low')}, {o.get('ci_high')}]" if o.get("trades", 0) > 1 else "n/a"
        L.append(f"| {n} | {o.get('trades', 0)} | {o.get('net_expectancy_R', 'n/a')} | {ci} | {r['verdict']} |")
    L += ["", f"Baseline (1h-trend, one trade/day) OOS: {rep['baseline_hourly_trend']['out_of_sample']}", "",
          f"**Options research: {rep['options_profitability_research']['status']}** - {rep['options_profitability_research']['reason']}", ""]
    L += ["Limitations:"] + [f"- {x}" for x in rep["limitations"]]
    return "\n".join(L)
