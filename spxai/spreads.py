"""Credit vertical spread IDEAS on SPY (bear call spreads, bull put spreads, iron condors). PRACTICE ONLY.

Real SPY option quotes are not available under the $0 data requirement, so every price here is an ESTIMATE from the
Black-Scholes formula using a volatility assumption. Real credits will differ (they usually pay more on the put side
because of the volatility "skew", and implied volatility is usually above the recent realized figure). This module
never places an order and never claims an edge: it only lays out strikes, risk, an estimated credit and an estimated
chance of keeping it, next to how often SPY really moved that far in the saved history.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from . import calendar as cal

RATE = 0.04  # assumed risk-free rate
DIV_YIELD = 0.012  # assumed SPY dividend yield
SLIPPAGE = 0.02  # per share, taken off the estimated credit (you rarely fill at the mid price)
MIN_CREDIT_FRAC = 0.25  # floor: collect at least a quarter of the width, else the risk is lopsided
SESSIONS_PER_YEAR = 252


# ------------------------------------------------------------------ pricing

def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float, kind: str) -> float:
    """European option price, Black-Scholes with continuous dividend yield. kind is 'call' or 'put'."""
    t_years = max(t_years, 1e-6)
    sigma = max(sigma, 1e-4)
    sq = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (RATE - DIV_YIELD + 0.5 * sigma * sigma) * t_years) / sq
    d2 = d1 - sq
    df_r, df_q = math.exp(-RATE * t_years), math.exp(-DIV_YIELD * t_years)
    if kind == "call":
        return spot * df_q * _ncdf(d1) - strike * df_r * _ncdf(d2)
    return strike * df_r * _ncdf(-d2) - spot * df_q * _ncdf(-d1)


def prob_above(spot: float, level: float, t_years: float, sigma: float) -> float:
    """Chance SPY finishes ABOVE `level` at expiry (risk-neutral lognormal, no drift view)."""
    t_years = max(t_years, 1e-6)
    sq = max(sigma, 1e-4) * math.sqrt(t_years)
    d2 = (math.log(spot / level) + (RATE - DIV_YIELD - 0.5 * sigma * sigma) * t_years) / sq
    return _ncdf(d2)


# --------------------------------------------------------------- market stats

def daily_from_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily open/high/low/close from 5-minute bars (index = timestamps)."""
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    d = bars.copy()
    d["_d"] = pd.DatetimeIndex(d.index).date
    g = d.groupby("_d")
    out = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()})
    out.index = pd.DatetimeIndex(out.index)
    return out


def realized_vol(daily: pd.DataFrame, window: int = 20) -> float | None:
    """Annualised volatility of daily closes over the last `window` sessions (None if too little data)."""
    if daily is None or len(daily) < window + 1:
        return None
    r = (daily.close / daily.close.shift(1)).apply(math.log).dropna().tail(window)
    sd = float(r.std())
    return sd * math.sqrt(SESSIONS_PER_YEAR) if sd == sd and sd > 0 else None


def key_levels(daily: pd.DataFrame, spot: float, ema_hourly: float | None = None, vwap: float | None = None,
               today: dt.date | None = None) -> dict:
    """Named price levels used to place strikes. Anything unavailable is left out."""
    lv: dict[str, float] = {}
    if daily is not None and len(daily):
        today = today or dt.date.today()
        done = daily[pd.DatetimeIndex(daily.index).date < today]  # completed sessions only
        if len(done):
            prev = done.iloc[-1]
            lv["yesterday's high"], lv["yesterday's low"] = float(prev.high), float(prev.low)
        daily = done
    if daily is not None and len(daily) >= 5:
        w = daily.tail(5)
        lv["5-day high"], lv["5-day low"] = float(w.high.max()), float(w.low.min())
    if daily is not None and len(daily) >= 20:
        w = daily.tail(20)
        lv["20-day high"], lv["20-day low"] = float(w.high.max()), float(w.low.min())
    if ema_hourly is not None and ema_hourly == ema_hourly:
        lv["hourly trend line"] = float(ema_hourly)
    if vwap is not None and vwap == vwap:
        lv["day's average price"] = float(vwap)
    return {k: round(v, 2) for k, v in lv.items() if v and v > 0}


def bias(spot: float, ema_hourly: float | None, vwap: float | None = None) -> str:
    """'bearish', 'bullish' or 'neutral' from price vs the hourly trend line (and the day's average price)."""
    if ema_hourly is None or ema_hourly != ema_hourly:
        return "neutral"
    below = spot < ema_hourly * 0.998 and (vwap is None or spot <= vwap)
    above = spot > ema_hourly * 1.002 and (vwap is None or spot >= vwap)
    return "bearish" if below else ("bullish" if above else "neutral")


def trading_days_between(start: dt.date, end: dt.date) -> int:
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if cal.is_trading_day(d):
            n += 1
    return n


def expiry_dates(today: dt.date, targets=(3, 10)) -> list[dt.date]:
    """Weekly (Friday) expiries at least `target` calendar days out; steps back to a trading day if Friday is a holiday."""
    out = []
    for t in targets:
        d = today + dt.timedelta(days=t)
        d += dt.timedelta(days=(4 - d.weekday()) % 7)
        while not cal.is_trading_day(d):
            d -= dt.timedelta(days=1)
        if d not in out:
            out.append(d)
    return out


def breach_rate(daily: pd.DataFrame, distance_pct: float, n_days: int, side: str) -> tuple[float, int] | None:
    """How often, in the saved history, SPY traded beyond `distance_pct` (e.g. 0.02 = 2%) from a day's close
    at any time in the next `n_days` sessions. side='up' looks at highs, 'down' at lows. Returns (fraction, windows)."""
    if daily is None or len(daily) < n_days + 30 or n_days < 1:
        return None
    c, h, lo = daily.close.values, daily.high.values, daily.low.values
    hit = tot = 0
    for i in range(len(c) - n_days):
        tot += 1
        if side == "up":
            hit += int(h[i + 1: i + 1 + n_days].max() >= c[i] * (1 + distance_pct))
        else:
            hit += int(lo[i + 1: i + 1 + n_days].min() <= c[i] * (1 - distance_pct))
    return (hit / tot, tot) if tot else None


# -------------------------------------------------------------------- ideas

def _snap(level: float, side: str) -> int:
    """Whole-dollar strike just beyond `level` (above for calls, below for puts)."""
    return math.floor(level) + 1 if side == "call" else math.ceil(level) - 1


def _short_strike(spot: float, em: float, k: float, levels: dict[str, float], side: str) -> tuple[int, str]:
    """Short strike about k expected-moves away, pushed just beyond the nearest key level past that point."""
    target = spot + k * em if side == "call" else spot - k * em
    if side == "call":
        beyond = sorted((v, n) for n, v in levels.items() if v >= target)
    else:
        beyond = sorted(((v, n) for n, v in levels.items() if v <= target), reverse=True)
    if beyond:
        lvl, name = beyond[0]
        # only use the level when it is reasonably close to the target (within half an expected move)
        if abs(lvl - target) <= 0.5 * em:
            return _snap(lvl, side), name
    return _snap(target, side), f"{k:g}x the typical move"


def _leg_price(spot, strike, t, sigma, kind):
    return bs_price(spot, strike, t, sigma, kind)


def build_ideas(spot: float, sigma: float, today: dt.date, levels: dict[str, float], bias_label: str = "neutral",
                daily: pd.DataFrame | None = None, width: int = 1, risk_usd: float = 125.0,
                expiries: list[dt.date] | None = None, now: pd.Timestamp | None = None) -> list[dict]:
    """Returns a list of idea dicts (see keys below), best-fit first. All dollar figures are per ONE contract (100 shares)."""
    ideas: list[dict] = []
    width = max(int(width), 1)
    for exp in (expiries or expiry_dates(today)):
        cal_days = max((exp - today).days, 1)
        t = cal_days / 365.0
        n_sess = max(trading_days_between(today, exp), 1)
        em = spot * sigma * math.sqrt(t)  # one "typical move" over the period
        sides = {}
        for tier, k in (("Closer", 0.5), ("Standard", 0.8), ("Safer", 1.2)):
            for side in ("call", "put"):
                short, why = _short_strike(spot, em, k, levels, side)
                long_ = short + width if side == "call" else short - width
                credit = round(max(_leg_price(spot, short, t, sigma, side) - _leg_price(spot, long_, t, sigma, side) - SLIPPAGE, 0.0), 2)
                be = short + credit if side == "call" else short - credit
                p_keep = (1 - prob_above(spot, be, t, sigma)) if side == "call" else prob_above(spot, be, t, sigma)
                dist = abs(be - spot) / spot
                hist = breach_rate(daily, dist, n_sess, "up" if side == "call" else "down")
                max_loss = (width - credit) * 100
                contracts = int(risk_usd // max_loss) if max_loss > 0 else 0
                frac = credit / width
                flags = []
                if frac < MIN_CREDIT_FRAC:
                    flags.append("pays too little for the risk (under a quarter of the width)")
                if contracts < 1:
                    flags.append("one contract risks more than your per-trade limit")
                with_trend = (side == "call" and bias_label == "bearish") or (side == "put" and bias_label == "bullish")
                against = (side == "call" and bias_label == "bullish") or (side == "put" and bias_label == "bearish")
                if against:
                    flags.append("goes against the current trend")
                rec = {
                    "kind": "Bear call spread" if side == "call" else "Bull put spread",
                    "side": side, "tier": tier, "expiry": exp, "days": cal_days, "sessions": n_sess,
                    "short": short, "long": long_, "width": width, "anchor": why,
                    "credit": round(credit, 2), "max_profit": round(credit * 100, 2), "max_loss": round(max_loss, 2),
                    "breakeven": round(be, 2), "credit_frac": round(frac, 3),
                    "p_keep": round(p_keep, 3), "hist_breach": None if hist is None else round(hist[0], 3),
                    "hist_windows": None if hist is None else hist[1],
                    "contracts_in_budget": contracts, "with_trend": with_trend, "against_trend": against, "flags": flags,
                    "score": (2 if with_trend else 0) - (2 if against else 0) - len(flags) + (0.25 if tier == "Standard" else 0),
                }
                rec["legs"] = [(short, side, "SELL"), (long_, side, "BUY")]
                rec["ticket"] = ticket_text(rec)
                ideas.append(rec)
                sides[(tier, side)] = rec
        # iron condor = standard call spread + standard put spread
        c, p = sides[("Standard", "call")], sides[("Standard", "put")]
        credit = round(c["credit"] + p["credit"], 2)
        max_loss = (width - credit) * 100
        flags = []
        if credit / width < MIN_CREDIT_FRAC:
            flags.append("pays too little for the risk (under a quarter of the width)")
        if max_loss <= 0 or int(risk_usd // max_loss) < 1:
            flags.append("one contract risks more than your per-trade limit")
        if bias_label != "neutral":
            flags.append("a trending market is a poor fit for a range bet")
        if sigma and (c["short"] - p["short"]) < 2 * em:
            flags.append("the two sold strikes are closer together than a typical move")
        lo_be, hi_be = round(p["short"] - credit, 2), round(c["short"] + credit, 2)
        ic = {
            "kind": "Iron condor", "side": "both", "tier": "Standard", "expiry": exp, "days": cal_days, "sessions": n_sess,
            "short": f'{p["short"]}p / {c["short"]}c', "long": f'{p["long"]}p / {c["long"]}c', "width": width,
            "anchor": "both standard spreads", "credit": round(credit, 2), "max_profit": round(credit * 100, 2),
            "max_loss": round(max_loss, 2), "breakeven": f"{lo_be} to {hi_be}", "credit_frac": round(credit / width, 3),
            "p_keep": round(max(0.0, prob_above(spot, lo_be, t, sigma) - prob_above(spot, hi_be, t, sigma)), 3),
            "hist_breach": None, "hist_windows": None,
            "contracts_in_budget": int(risk_usd // max_loss) if max_loss > 0 else 0,
            "with_trend": False, "against_trend": False, "flags": flags,
            "score": -1 - len(flags), "legs": c["legs"] + p["legs"],
        }
        ic["ticket"] = ticket_text(ic)
        ideas.append(ic)
    ideas.sort(key=lambda r: (-r["score"], r["expiry"], r["tier"]))
    return ideas


def verdict(idea: dict) -> tuple[str, str]:
    """(label, tone) for display. tone is good / warn / bad."""
    f = idea["flags"]
    if idea["against_trend"] or any("too little" in x for x in f) or any("limit" in x for x in f):
        return ("Skip it" if len(f) > 1 else "Weak idea", "bad" if len(f) > 1 else "warn")
    if f:
        return "Weak idea", "warn"
    return ("Fits the trend" if idea["with_trend"] else "Reasonable"), "good"


def ticket_text(i: dict) -> str:
    """Plain wording to copy into a PAPER trading account."""
    exp = i["expiry"].strftime("%b %d %Y") if hasattr(i["expiry"], "strftime") else str(i["expiry"])
    if i["kind"] == "Iron condor":
        (c_s, _, _), (c_l, _, _), (p_s, _, _), (p_l, _, _) = i["legs"]
        return (f"PAPER ONLY. SPY {exp} iron condor: SELL {c_s} call / BUY {c_l} call, and SELL {p_s} put / BUY {p_l} put. "
                f"Limit credit about ${i['credit']:.2f} or better. Most you can lose per contract about ${i['max_loss']:.0f}.")
    return (f"PAPER ONLY. SPY {exp}: SELL the {i['short']} {i['side']} and BUY the {i['long']} {i['side']} "
            f"(one {i['kind'].lower()}). Limit credit about ${i['credit']:.2f} or better. "
            f"Most you can lose per contract about ${i['max_loss']:.0f}. Keep if SPY stays "
            f"{'below' if i['side'] == 'call' else 'above'} {i['short']} at expiry.")


def payoff(idea: dict, spot: float, points: int = 121) -> tuple[list[float], list[float]]:
    """(prices, profit per contract in dollars at expiry) for charting."""
    lo, hi = spot * 0.93, spot * 1.07
    xs = [lo + (hi - lo) * k / (points - 1) for k in range(points)]
    cr = idea["credit"]
    ys = []
    for x in xs:
        pnl = cr
        for strike, kind, action in idea["legs"]:
            intrinsic = max(x - strike, 0.0) if kind == "call" else max(strike - x, 0.0)
            pnl += -intrinsic if action == "SELL" else intrinsic
        ys.append(round(pnl * 100, 2))
    return xs, ys
