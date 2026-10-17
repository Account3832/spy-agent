"""Plain-English wording and number formatting for the dashboard. Pure functions, unit-tested.

Nothing here changes any trading logic. It only turns the engine's technical output
(R-multiples, verdict codes, strategy ids) into words and dollar figures a beginner can read.
"""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from . import calendar as cal
from .config import TZ

STRATEGIES = {
    "trend_pullback": ("Trend Pullback",
                       "Joins the trend after a small dip: waits for price to pull back to its average, then turn back with the trend."),
    "orb_retest": ("Opening Range Retest",
                   "Waits for price to break out of the first 30 minutes' range, come back to test it, and hold."),
    "failed_breakout": ("Failed Breakout",
                        "Bets against a breakout that fizzles and falls back into the range."),
    "range_reversion": ("Range Reversion",
                        "On quiet sideways days, buys near the bottom of the range and sells near the top."),
    "vol_expansion": ("Volatility Breakout",
                      "Jumps in when a quiet stretch suddenly turns into a big move."),
    "orb_breakout": ("Opening Range Breakout",
                     "Buys the first break above the first 30 minutes' high (or sells the first break below its low) when the 1-hour trend agrees."),
    "vwap_reclaim": ("VWAP Reclaim",
                     "In a trend, waits for price to dip through the day's average price and snap back, then joins the trend."),
    "gap_fade": ("Gap Fade",
                 "After a big overnight gap, bets that price drifts back toward yesterday's close once it starts to turn."),
    "prior_day_break": ("Yesterday's High/Low Break",
                        "Joins the first break above yesterday's high (or below yesterday's low) when the 1-hour trend agrees."),
    "steady_scalper": ("Steady Scalper (high win rate test)",
                       "Same entries as Trend Pullback, but a wide stop and a very near target. It wins often, but each loss is big. "
                       "Built to show what a high win rate costs."),
    "baseline_hourly_trend": ("Simple 1-hour trend (comparison)",
                              "The simplest rule: follow the 1-hour trend once a day. A yardstick the others should beat."),
}

VERDICTS = {
    "NOT_DEMONSTRATED": ("Not proven", "warn",
                         "On data the rules had not seen, the result could just be luck. Do not rely on it."),
    "INSUFFICIENT_SAMPLE": ("Too few trades", "muted",
                            "Not enough trades to say anything. At least 100 are needed."),
    "FAILED_OOS": ("Failed the test", "bad",
                   "It lost money on data the rules had not seen."),
    "UNDERLYING_EDGE_SUPPORTED": ("Passed (shares only)", "good",
                                  "It passed the test on SPY shares. This says nothing about options."),
}

REGIMES = {"trend": "Trending market", "range": "Sideways market", "transition": "Mixed or changing market"}
DIRECTIONS = {"LONG": "Buy (bet price rises)", "SHORT": "Sell (bet price falls)"}
EXITS = {"stop": "stopped out", "BE-stop": "stopped at break-even", "T1": "hit target 1", "T2": "hit target 2",
         "time": "closed at the time limit", "maxhold": "closed after holding too long", "data_end": "closed at end of data"}

GLOSSARY = [
    ("Win / loss", "A trade is a win if it made money after costs, and a loss if it lost money."),
    ("Win rate", "Wins out of every 100 trades. A win rate under 50% is fine if wins are bigger than losses."),
    ("1R (one unit of risk)", "The amount you risk on one trade. Here it is {risk} (0.5% of a $25,000 paper account)."),
    ("Average win / average loss", "How much a typical winning or losing trade made or lost."),
    ("Worst losing stretch", "The biggest fall from a high point in the running total. It shows how bad a rough patch got."),
    ("Plausible range", "Where the true average per trade probably lies. If the range includes $0, the result could be luck."),
    ("Not proven", "The fair test (on data the rules were not tuned on) did not show a reliable edge."),
    ("Paper trading", "Fake money. Nothing here places a real order."),
]


# ------------------------------------------------------------------ numbers

def usd(x, sign: bool = True, decimals: int = 0) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    s = f"{abs(x):,.{decimals}f}"
    if round(x, decimals) == 0:
        return f"${s}"
    return (("+" if x > 0 else "-") if sign else ("-" if x < 0 else "")) + f"${s}"


def pct(x, decimals: int = 0) -> str:
    return "n/a" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x * 100:.{decimals}f}%"


def r_to_usd(r, risk) -> float | None:
    return None if r is None or (isinstance(r, float) and pd.isna(r)) else float(r) * float(risk)


# -------------------------------------------------------------------- names

def strategy_name(key: str) -> str:
    return STRATEGIES.get(key, (key.replace("_", " ").title(), ""))[0]


def strategy_blurb(key: str) -> str:
    return STRATEGIES.get(key, ("", ""))[1]


def verdict_info(code: str | None) -> tuple[str, str, str]:
    return VERDICTS.get(code or "", ("Not tested", "muted", "No fair test result is available for this one."))


def bucket_label(b: str) -> str:
    m = re.fullmatch(r"(\d\d):(\d\d)-(\d\d):(\d\d)", str(b))
    if not m:
        return str(b)

    def t(h, mi):
        h = int(h)
        return f"{h % 12 or 12}:{mi} {'AM' if h < 12 else 'PM'}"
    lab = f"{t(m[1], m[2])} to {t(m[3], m[4])}"
    return lab + (" (first hour)" if str(b).startswith("09:45") else "")


def exit_text(s: str) -> str:
    parts = [p for p in str(s).split("|") if p]
    names = [EXITS.get(p, p) for p in parts]
    if not names:
        return "n/a"
    if len(names) == 1 or names[0] == names[1]:
        return names[0].capitalize()
    return f"Half {names[0]}, half {names[1]}".replace("Half ", "First half ", 1).replace(", half ", ", second half ")


_JARGON = [(r"\bEMA ?21\b", "its 21-period average"), (r"\bEMA ?9\b", "its 9-period average"), (r"\bATR\b", "a typical candle's range"),
           (r"\b1h\b", "1-hour"), (r"prior bar extreme", "the previous candle's high or low"), (r"opening range", "the first 30 minutes' range"),
           (r"\bVWAP\b", "the day's average price"), (r"\bORB\b", "opening range")]


def plain_text(t: str) -> str:
    for pat, rep in _JARGON:
        t = re.sub(pat, rep, t)
    return t[:1].upper() + t[1:] if t else t


def plain_requirement(f: dict) -> str:
    name = re.sub(r"^\d+(/\d+)?\.\s*", "", str(f.get("requirement", "")))
    detail = str(f.get("detail", "")).strip()
    return plain_text(name) + (f": {plain_text(detail)}" if detail else "")


# ----------------------------------------------------------------- top card

def decision_view(plan: dict) -> dict:
    dec = plan.get("decision")
    if dec == "CALL":
        return {"tone": "good", "headline": "Bullish setup",
                "sub": "The rules see a possible rise. In this paper system that means a simulated purchase of SPY shares."}
    if dec == "PUT":
        return {"tone": "bad", "headline": "Bearish setup",
                "sub": "The rules see a possible fall. In this paper system that means a simulated short sale of SPY shares."}
    if dec in (None, "no run yet", "SKIPPED"):
        return {"tone": "muted", "headline": "Waiting for the first check",
                "sub": "The checker runs by itself every 5 minutes while the market is open."}
    return {"tone": "neutral", "headline": "No trade right now",
            "sub": "The rules do not see a good setup. Staying out is a normal, frequent outcome."}


def _parse_ts(x) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(x)
        if pd.isna(t):
            return None
        return t.tz_localize(TZ) if t.tzinfo is None else t.tz_convert(TZ)
    except Exception:  # noqa: BLE001
        return None


def freshness(plan: dict, now: pd.Timestamp) -> tuple[str, str]:
    ts = _parse_ts(plan.get("timestamp"))
    if ts is None:
        return "No check recorded yet", "muted"
    when = f"{ts.strftime('%a')} {ts.strftime('%I:%M %p').lstrip('0')}"
    if plan.get("data_status") == "delayed":
        return f"Delayed data (unofficial source) · checked {when}", "warn"
    if not cal.is_open(now):
        return f"Market closed · last check {when}", "neutral"
    age = (now - ts).total_seconds() / 60
    if age <= 15:
        return f"Live data · checked {max(0, round(age))} min ago", "good"
    return f"Check is {round(age)} min old · last check {when}", "warn"


def next_check_text(now: pd.Timestamp) -> str:
    if cal.is_open(now):
        nxt = now.ceil("5min") + pd.Timedelta(seconds=30)
        return f"after the next 5-minute candle closes (about {nxt.strftime('%I:%M %p').lstrip('0')} ET)"
    d = now.date()
    for _ in range(10):
        b = cal.session_bounds(d)
        if b is not None and (d > now.date() or now < b[0]):
            day = "today" if d == now.date() else pd.Timestamp(d).strftime("%A")
            return f"{day} at 9:35 AM ET, shortly after the market opens"
        d += dt.timedelta(days=1)
    return "when the market next opens"


# --------------------------------------------------------- results tables

def scoreboard(ts: dict, bt: dict, risk: float, live=()) -> pd.DataFrame:
    rows = []
    for key, r in (ts.get("strategies") or {}).items():
        v = ((bt or {}).get("strategies") or {}).get(key, {}).get("verdict")
        rows.append({"Strategy": strategy_name(key), "Trades": r["trades"], "Wins": r["wins"], "Losses": r["losses"],
                     "Win rate": round((r["win_rate"] or 0) * 100, 1),
                     "Average win ($)": round(r["avg_win_R"] * risk, 0), "Average loss ($)": round(r["avg_loss_R"] * risk, 0),
                     "Average per trade ($)": round((r["net_exp_R"] or 0) * risk, 2),
                     "Total result ($)": round(r["total_net_R"] * risk, 0),
                     "Worst losing stretch ($)": round(-(r.get("max_drawdown_R") or 0) * risk, 0),
                     "Fair test": verdict_info(v)[0] if v else "comparison only",
                     "_tone": verdict_info(v)[1] if v else "muted", "_key": key,
                     "Status": ("Comparison" if key == "baseline_hourly_trend" else
                                ("Live practice" if key in live else "Research only"))})
    df = pd.DataFrame(rows)
    return df.sort_values("Total result ($)", ascending=False).reset_index(drop=True) if len(df) else df


def condition_table(groups: dict, kind: str, risk: float) -> pd.DataFrame:
    def lab(k):
        if kind == "direction":
            return DIRECTIONS.get(k, k)
        if kind == "regime":
            return REGIMES.get(k, k)
        if kind == "tod_bucket":
            return bucket_label(k)
        return k
    rows = [{"Group": lab(k), "Trades": g["trades"], "Wins": g["wins"], "Losses": g["losses"],
             "Win rate": round((g["win_rate"] or 0) * 100, 1), "Total ($)": round(g["total_net_R"] * risk, 0),
             "Average per trade ($)": round((g["net_exp_R"] or 0) * risk, 2), "_k": k} for k, g in groups.items()]
    df = pd.DataFrame(rows)
    if len(df) and kind in ("tod_bucket", "month"):
        return df.sort_values("_k").reset_index(drop=True)
    return df.sort_values("Total ($)", ascending=False).reset_index(drop=True) if len(df) else df


def best_worst(df: pd.DataFrame, noun: str, min_trades: int = 30) -> str:
    d = df[df["Trades"] >= min_trades] if len(df) else df
    if len(d) < 2:
        return f"Not enough trades in each {noun} to compare."
    b, w = d.loc[d["Average per trade ($)"].idxmax()], d.loc[d["Average per trade ($)"].idxmin()]
    return (f"Best {noun}: {b['Group']} ({usd(b['Average per trade ($)'], decimals=2)} per trade over {int(b['Trades'])} trades). "
            f"Weakest: {w['Group']} ({usd(w['Average per trade ($)'], decimals=2)} per trade over {int(w['Trades'])} trades).")


def equity_curves(tl: pd.DataFrame) -> pd.DataFrame:
    if tl is None or len(tl) == 0:
        return pd.DataFrame()
    t = tl.copy()
    t["date"] = pd.to_datetime(t["date"])
    daily = t.groupby(["date", "strategy"]).pnl_usd_illustrative.sum().unstack("strategy").fillna(0).sort_index()
    out = daily.cumsum()
    out.columns = [strategy_name(c) for c in out.columns]
    return out


def trades_view(tl: pd.DataFrame) -> pd.DataFrame:
    if tl is None or len(tl) == 0:
        return pd.DataFrame()
    t = tl.copy()
    et = pd.to_datetime(t["entry_time"], utc=True).dt.tz_convert(TZ)
    xt = pd.to_datetime(t["exit_time"], utc=True).dt.tz_convert(TZ)
    fmt = lambda s: s.dt.strftime("%b %d, %I:%M %p").str.replace(" 0", " ", regex=False)  # noqa: E731
    return pd.DataFrame({
        "Entered": fmt(et), "Exited": fmt(xt), "Minutes held": t["minutes_held"],
        "Buy or sell": t["direction"].map(lambda d: "Buy" if d == "LONG" else "Sell"),
        "Result": t["result"].map({"WIN": "Win", "LOSS": "Loss", "FLAT": "Even"}),
        "Result ($)": t["pnl_usd_illustrative"], "How it ended": t["exit_reason"].map(exit_text),
        "Market type": t["regime"].map(lambda r: REGIMES.get(r, r)), "Time of day": t["tod_bucket"].map(bucket_label),
        **({"Got in at ($)": t["entry"], "Stop was ($)": t["stop_price"], "Target 1 ($)": t["target1_price"],
            "Target 2 ($)": t["target2_price"], "Got out at ($)": t["exit_price"]} if "stop_price" in t else {})})


def reliability_table(bt: dict, risk: float) -> pd.DataFrame:
    rows = []
    for key, r in ((bt or {}).get("strategies") or {}).items():
        o = r.get("out_of_sample_reported_once", {})
        v = verdict_info(r.get("verdict"))
        rows.append({"Strategy": strategy_name(key), "Trades tested": o.get("trades", 0),
                     "Average per trade ($)": round((o.get("net_expectancy_R") or 0) * risk, 2),
                     "Plausible range, low ($)": round((o.get("ci_low") or 0) * risk, 2),
                     "Plausible range, high ($)": round((o.get("ci_high") or 0) * risk, 2),
                     "Verdict": v[0], "What it means": v[2]})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ trade chart

def trade_option_label(r) -> str:
    et = pd.to_datetime(r["entry_time"], utc=True).tz_convert(TZ)
    when = et.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    side = "Buy" if r["direction"] == "LONG" else "Sell"
    res = {"WIN": "Win", "LOSS": "Loss", "FLAT": "Even"}.get(r["result"], r["result"])
    return f"{when}  ·  {side}  ·  {res} {usd(r['pnl_usd_illustrative'])}"


def trade_levels(entry: float, risk: float, k: int, t1_r: float, t2_r: float, net_R: float) -> dict:
    """Stop, targets and average exit price implied by a trade's entry, risk and result (same maths as the simulator)."""
    return {"stop_price": round(entry - k * risk, 2), "target1_price": round(entry + k * t1_r * risk, 2),
            "target2_price": round(entry + k * t2_r * risk, 2), "exit_price": round(entry + k * net_R * risk, 2)}


def paper_trade_dict(row, registry: dict, budget_hint: float = 0.0) -> dict:
    """Turn a closed paper-trade row into the same shape the 2-year log uses, so one chart can draw both."""
    k = int(row["dir"])
    cls = registry.get(row["strategy"])
    t1_r, t2_r = (cls().t1_r, cls().t2_r) if cls else (1.5, 3.0)
    lv = trade_levels(float(row["entry"]), float(row["risk_per_share"]), k, t1_r, t2_r, float(row["net_R"]))
    pnl = float(row["pnl_usd"]) if "pnl_usd" in row and pd.notna(row["pnl_usd"]) else float(row["net_R"]) * budget_hint
    et = pd.to_datetime(row["entry_time"], utc=True).tz_convert(TZ)
    return {"date": str(et.date()), "entry_time": str(row["entry_time"]), "exit_time": str(row["exit_time"]),
            "direction": "LONG" if k == 1 else "SHORT", "entry": float(row["entry"]), **lv,
            "result": _wl_label(float(row["net_R"])), "pnl_usd_illustrative": pnl, "exit_reason": str(row["exit_reason"]),
            "strategy": row["strategy"]}


def _wl_label(r: float) -> str:
    return "WIN" if r > 0 else ("LOSS" if r < 0 else "FLAT")


def win_rate_lesson(sb: pd.DataFrame) -> str | None:
    """Shows, from the user's own results, that a high win rate and making money are different things."""
    need = {"steady_scalper", "trend_pullback"}
    if sb is None or len(sb) == 0 or not need <= set(sb["_key"]):
        return None
    a = sb[sb["_key"] == "steady_scalper"].iloc[0]
    b = sb[sb["_key"] == "trend_pullback"].iloc[0]
    return (f"**A high win rate is not the same as making money.** The Steady Scalper wins {a['Win rate']:.0f} of every 100 trades, "
            f"far more than Trend Pullback ({b['Win rate']:.0f} of 100). But its wins are small and its losses are big: its total result is "
            f"{usd(a['Total result ($)'])}, against {usd(b['Total result ($)'])} for Trend Pullback. "
            f"Chasing a very high win rate usually means taking a small profit and risking a lot, and one bad day can erase many wins.")
