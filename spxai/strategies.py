"""Explicit, versioned trading hypotheses (five live candidates, four research candidates, one high-win-rate demonstration). These are RESEARCH CANDIDATES, not assumed winners.

Each strategy turns completed-bar features into signals at the bar CLOSE. The backtester
enters at the NEXT bar's open. Filters can be toggled for ablation tests.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ALL_FILTERS = ("htf", "vwap", "adx", "rvol")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    version: str
    hypothesis: str
    conditions: str
    trigger: str
    confirmation: str
    invalidation: str
    stop_logic: str
    targets: str
    time_exit: str
    contract_criteria: str
    risk_constraints: str
    holding_period: str
    params: dict = field(default_factory=dict)

    def param_hash(self) -> str:
        return hashlib.sha1(json.dumps(self.params, sort_keys=True).encode()).hexdigest()[:8]

    def label(self) -> str:
        return f"{self.name}@{self.version}#{self.param_hash()}"


class Strategy:
    spec: StrategySpec
    default_filters: tuple = ()
    stop_atr = 1.5
    t1_r = 1.5
    t2_r = 3.0
    max_hold_bars = 36

    def __init__(self, filters=None, stop_atr=None, t1_r=None, t2_r=None, **params):
        self.filters = tuple(self.default_filters if filters is None else filters)
        if stop_atr is not None:
            self.stop_atr = stop_atr
        if t1_r is not None:
            self.t1_r = t1_r
        if t2_r is not None:
            self.t2_r = t2_r
        self.params = {**self.spec.params, **params}

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def version(self) -> str:
        return self.spec.version

    def label(self) -> str:
        p = {**self.params, "stop_atr": self.stop_atr, "t1_r": self.t1_r, "t2_r": self.t2_r, "filters": list(self.filters)}
        return f"{self.name}@{self.version}#{hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:8]}"

    # subclasses implement raw long/short boolean series
    def raw(self, d: pd.DataFrame) -> tuple[pd.Series, pd.Series, str]:
        raise NotImplementedError

    def forming(self, d: pd.DataFrame) -> str | None:  # optional "setup forming" text for the last bar
        return None

    def _filter(self, d, longs, shorts):
        f = self.filters
        if "htf" in f:
            longs, shorts = longs & (d.h_dir == 1), shorts & (d.h_dir == -1)
        if "vwap" in f:
            longs, shorts = longs & (d.close > d.vwap), shorts & (d.close < d.vwap)
        if "adx" in f:
            ok = d.adx >= 20
            longs, shorts = longs & ok, shorts & ok
        if "rvol" in f:
            ok = d.rvol >= 1.0
            longs, shorts = longs & ok, shorts & ok
        return longs, shorts

    def signals(self, d: pd.DataFrame, entry_start="09:45", entry_end="14:55") -> pd.DataFrame:
        longs, shorts, trig = self.raw(d)
        longs, shorts = self._filter(d, longs.fillna(False), shorts.fillna(False))
        win = (d.tod >= entry_start) & (d.tod <= entry_end) & d.atr.notna()
        longs, shorts = longs & win, shorts & win
        direction = np.where(longs & ~shorts, 1, np.where(shorts & ~longs, -1, 0))
        return pd.DataFrame({"dir": direction, "trigger": np.where(direction != 0, trig, "")}, index=d.index)


class TrendPullback(Strategy):
    spec = StrategySpec(
        "trend_pullback", "1.0",
        "When the 1-hour trend and 5-minute trend agree, a shallow pullback to the 21-EMA that reclaims "
        "its prior bar high tends to continue in the trend direction.",
        "1h close above its 20-EMA with rising slope (long), 5m EMA9 > EMA21; mirrored for short.",
        "5m bar closes above the prior bar's high after touching EMA21 within the last 6 bars, closing above EMA21 and its own open.",
        "1h trend must agree; price on the correct side of VWAP; ADX >= 20.",
        "Close back through EMA21 against the trade, or 1h trend flips.",
        "1.5 x ATR(14) from entry.", "T1 = 1.5R (half), T2 = 3R (half, stop to breakeven after T1).",
        "Flat at the mandatory exit time (15:45 ET) at the latest.",
        "Liquid same-day SPY options, delta near 0.30-0.40, spread <= configured max. Requires verified executable quotes.",
        "Max 3 trades/day, 1 concurrent, risk per trade from config.", "30 minutes to 3 hours")
    default_filters = ("htf", "vwap", "adx")

    def raw(self, d):
        touched_up = (d.low.rolling(6).min() <= d.ema21)
        touched_dn = (d.high.rolling(6).max() >= d.ema21)
        longs = (d.ema9 > d.ema21) & touched_up & (d.close > d.ema21) & (d.close > d.high.shift()) & (d.close > d.open)
        shorts = (d.ema9 < d.ema21) & touched_dn & (d.close < d.ema21) & (d.close < d.low.shift()) & (d.close < d.open)
        return longs, shorts, "pullback to EMA21 reclaimed prior bar extreme"

    def forming(self, d):
        r = d.iloc[-1]
        if r.h_dir != 0 and pd.notna(r.atr) and abs(r.close - r.ema21) <= 0.5 * r.atr and abs(r.ema9 - r.ema21) > 0:
            side = "up" if r.h_dir == 1 else "down"
            return f"1h trend {side}; price within 0.5 ATR of EMA21, waiting for a reclaim of the prior bar extreme"
        return None


class OrbRetest(Strategy):
    spec = StrategySpec(
        "orb_retest", "1.0",
        "A confirmed break of the first-30-minute range that retests the break level and holds tends to extend.",
        "Opening range (09:30-10:00) is complete; no signal before 10:05.",
        "Within 2-12 bars after a close beyond the range, a bar wicks back to the level (within 0.1 ATR) and closes beyond it in the break direction.",
        "Bar closes in the break direction (green for long); 1h trend not opposed.",
        "Close back inside the opening range.", "1.2 x ATR(14).", "T1 = 1.5R, T2 = 3R.",
        "Flat at the mandatory exit.", "Same as trend_pullback.", "Max 1 signal per direction per day.", "30 minutes to 3 hours")
    default_filters = ("vwap",)
    stop_atr = 1.2

    def raw(self, d):
        tol = 0.1 * d.atr
        brk_up = (d.close > d.or_high).astype(float)
        brk_dn = (d.close < d.or_low).astype(float)
        # a prior break 2..12 bars ago (shifted so the current bar is never its own break)
        prior_up = brk_up.shift(2).rolling(11, min_periods=1).max() > 0
        prior_dn = brk_dn.shift(2).rolling(11, min_periods=1).max() > 0
        longs = prior_up & (d.low <= d.or_high + tol) & (d.close > d.or_high) & (d.close > d.open)
        shorts = prior_dn & (d.high >= d.or_low - tol) & (d.close < d.or_low) & (d.close < d.open)
        # first signal per direction per day only
        longs = longs & (longs.groupby(d.date).cumsum() == 1)
        shorts = shorts & (shorts.groupby(d.date).cumsum() == 1)
        return longs, shorts, "opening-range break retested and held"

    def forming(self, d):
        r = d.iloc[-1]
        if pd.notna(r.or_high) and r.close > r.or_high:
            return "Price is above the opening range high; waiting for a retest that holds"
        if pd.notna(r.or_low) and r.close < r.or_low:
            return "Price is below the opening range low; waiting for a retest that holds"
        return None


class FailedBreakout(Strategy):
    spec = StrategySpec(
        "failed_breakout", "1.0",
        "A break of the opening range that fails and closes back inside traps late entrants and tends to reverse.",
        "Opening range complete; the break happened within the last 3 bars.",
        "A bar's high exceeded OR high (or low broke OR low) within 3 bars, and the current bar closes back inside the range with a bearish (bullish) body.",
        "Current bar closes beyond its own midpoint toward the reversal; ADX below 30 (not a runaway trend).",
        "A new extreme beyond the failed break.", "1.0 x ATR beyond entry.", "T1 = 1.5R, T2 = 3R.",
        "Flat at the mandatory exit.", "Same as trend_pullback.", "Max 1 per direction per day.", "15 minutes to 2 hours")
    stop_atr = 1.0
    default_filters = ()

    def raw(self, d):
        broke_hi = (d.high > d.or_high).astype(float).shift(1).rolling(3, min_periods=1).max() > 0
        broke_lo = (d.low < d.or_low).astype(float).shift(1).rolling(3, min_periods=1).max() > 0
        calm = d.adx < 30
        shorts = broke_hi & (d.close < d.or_high) & (d.close < d.open) & (d.close < (d.high + d.low) / 2) & calm
        longs = broke_lo & (d.close > d.or_low) & (d.close > d.open) & (d.close > (d.high + d.low) / 2) & calm
        shorts = shorts & (shorts.groupby(d.date).cumsum() == 1)
        longs = longs & (longs.groupby(d.date).cumsum() == 1)
        return longs, shorts, "opening-range break failed and closed back inside"


class RangeReversion(Strategy):
    spec = StrategySpec(
        "range_reversion", "1.0",
        "In low-trend (range) conditions, a close back inside a Bollinger band after an excursion tends to revert toward the mean.",
        "5m ADX < 18 (range regime).",
        "Previous bar closed outside the lower (upper) band and the current bar closes back inside it with RSI < 35 (> 65) on the prior bar.",
        "Current bar closes in the reversal direction.", "ADX rises above 25.",
        "1.0 x ATR(14).", "T1 = 1.0R, T2 = 2.0R.", "Flat at the mandatory exit.", "Same as trend_pullback.",
        "Max 3 trades/day.", "15 minutes to 90 minutes")
    stop_atr, t1_r, t2_r, max_hold_bars = 1.0, 1.0, 2.0, 18
    default_filters = ()

    def raw(self, d):
        rng = d.adx < 18
        longs = rng & (d.close.shift() < d.bb_lo.shift()) & (d.close > d.bb_lo) & (d.rsi.shift() < 35) & (d.close > d.open)
        shorts = rng & (d.close.shift() > d.bb_up.shift()) & (d.close < d.bb_up) & (d.rsi.shift() > 65) & (d.close < d.open)
        return longs, shorts, "close back inside Bollinger band in a range regime"


class VolExpansion(Strategy):
    spec = StrategySpec(
        "vol_expansion", "1.0",
        "A bar with abnormally large range that closes near its extreme and breaks the 20-bar high/low signals momentum that continues.",
        "Bar range >= 1.8 x prior ATR.",
        "Close in the top (bottom) 25% of the bar and beyond the prior 20-bar high (low).",
        "Relative volume >= 1.0 when volume is available.", "Close back inside the 20-bar range.",
        "1.5 x ATR(14).", "T1 = 1.5R, T2 = 3R.", "Flat at the mandatory exit.", "Same as trend_pullback.",
        "Max 2 per day.", "15 minutes to 2 hours")
    default_filters = ("rvol",)

    def raw(self, d):
        wide = d.range_atr >= 1.8
        pos = (d.close - d.low) / (d.high - d.low).replace(0, np.nan)
        longs = wide & (pos >= 0.75) & (d.close > d.hi20)
        shorts = wide & (pos <= 0.25) & (d.close < d.lo20)
        return longs, shorts, "wide-range bar closing at its extreme through the 20-bar range"


class BaselineHourlyTrend(Strategy):
    """Deliberately simple comparison: take one trade a day in the 1-hour trend direction at 10:30."""
    spec = StrategySpec(
        "baseline_hourly_trend", "1.0", "Naive benchmark; any real strategy should beat it.",
        "1h trend not flat.", "Entry on the 10:30 bar close in the 1h trend direction.", "None.", "None.",
        "1.5 x ATR(14).", "T1 = 1.5R, T2 = 3R.", "Flat at the mandatory exit.", "n/a", "1 per day", "up to a day")

    def raw(self, d):
        at = d.tod == "10:25"
        return at & (d.h_dir == 1), at & (d.h_dir == -1), "baseline: 1h trend direction at 10:30"


def _first_per_day(sig: pd.Series, d: pd.DataFrame) -> pd.Series:
    return sig & (sig.groupby(d.date).cumsum() == 1)


class OrbBreakout(Strategy):
    spec = StrategySpec(
        "orb_breakout", "1.0",
        "A decisive close beyond the first-30-minute range, in the direction of the 1-hour trend, tends to extend "
        "(the published 'opening range breakout' idea; effects can fade, so it must pass the same fair test).",
        "Opening range (09:30-10:00) is complete.",
        "First 5-minute close beyond the opening range high (low) of the day.",
        "1h trend agrees; price on the correct side of VWAP.",
        "Close back inside the opening range.", "1.5 x ATR(14).", "T1 = 1.5R (half), T2 = 3R (half).",
        "Flat at the mandatory exit.", "Same as trend_pullback.", "Max 1 signal per direction per day.", "30 minutes to 4 hours")
    default_filters = ("htf", "vwap")

    def raw(self, d):
        up = (d.close > d.or_high) & (d.close.shift() <= d.or_high)
        dn = (d.close < d.or_low) & (d.close.shift() >= d.or_low)
        return _first_per_day(up, d), _first_per_day(dn, d), "first close beyond the opening range"


class VwapReclaim(Strategy):
    spec = StrategySpec(
        "vwap_reclaim", "1.0",
        "In a 1-hour trend, a dip through VWAP that is reclaimed on the next close tends to resume the trend.",
        "1h trend direction is clear.",
        "Previous bar closed on the wrong side of VWAP; this bar closes back through it in its own direction with fast EMA beyond slow EMA.",
        "1h trend agrees.", "Close back through VWAP against the trade.", "1.5 x ATR(14).", "T1 = 1.5R, T2 = 3R.",
        "Flat at the mandatory exit.", "Same as trend_pullback.", "Max 2 signals per direction per day.", "30 minutes to 3 hours")
    default_filters = ("htf",)

    def raw(self, d):
        longs = (d.close.shift() < d.vwap.shift()) & (d.close > d.vwap) & (d.close > d.open) & (d.ema9 > d.ema21)
        shorts = (d.close.shift() > d.vwap.shift()) & (d.close < d.vwap) & (d.close < d.open) & (d.ema9 < d.ema21)
        longs = longs & (longs.groupby(d.date).cumsum() <= 2)
        shorts = shorts & (shorts.groupby(d.date).cumsum() <= 2)
        return longs, shorts, "price reclaimed VWAP in the direction of the trend"


class GapFade(Strategy):
    spec = StrategySpec(
        "gap_fade", "1.0",
        "After a gap of at least 0.2% from the prior close, price often drifts back toward the prior close once it starts turning that way.",
        "Opening gap >= 0.2% versus the prior close; price still on the gap side of the prior close and at least 1 ATR away from it.",
        "Before 11:30: bar closes beyond the prior bar's extreme toward the prior close, in its own direction, beyond the 9-period EMA.",
        "None beyond the trigger.", "Price moves a further 1 ATR away from the prior close.", "1.0 x ATR(14).", "T1 = 1.0R, T2 = 2.0R.",
        "Flat at the mandatory exit.", "Same as trend_pullback.", "Max 1 signal per direction per day.", "30 minutes to 3 hours")
    stop_atr, t1_r, t2_r = 1.0, 1.0, 2.0
    default_filters = ()

    def raw(self, d):
        day_open = d.groupby("date").open.transform("first")
        gap = day_open / d.pdc - 1
        early = d.tod <= "11:30"
        longs = early & (gap <= -0.002) & (d.close < d.pdc) & ((d.pdc - d.close) >= d.atr) \
            & (d.close > d.high.shift()) & (d.close > d.open) & (d.close > d.ema9)
        shorts = early & (gap >= 0.002) & (d.close > d.pdc) & ((d.close - d.pdc) >= d.atr) \
            & (d.close < d.low.shift()) & (d.close < d.open) & (d.close < d.ema9)
        return _first_per_day(longs, d), _first_per_day(shorts, d), "gap fading back toward the prior close"


class PriorDayBreak(Strategy):
    spec = StrategySpec(
        "prior_day_break", "1.0",
        "A first close beyond yesterday's high (low), with the 1-hour trend behind it, tends to continue.",
        "Prior-day high and low are known.",
        "First 5-minute close of the day beyond the prior-day high (low), having been inside it on the previous close.",
        "1h trend agrees; price on the correct side of VWAP.",
        "Close back inside the prior-day range.", "1.5 x ATR(14).", "T1 = 1.5R, T2 = 3R.",
        "Flat at the mandatory exit.", "Same as trend_pullback.", "Max 1 signal per direction per day.", "30 minutes to 4 hours")
    default_filters = ("htf", "vwap")

    def raw(self, d):
        up = (d.close > d.pdh) & (d.close.shift() <= d.pdh)
        dn = (d.close < d.pdl) & (d.close.shift() >= d.pdl)
        return _first_per_day(up, d), _first_per_day(dn, d), "first close beyond the prior-day high/low"


class SteadyScalper(TrendPullback):
    """Same entries as trend_pullback, but a wide stop and a very near target. Built to SHOW the trade-off:
    it wins often, yet each loss is large. Research only: its reward is too small versus risk to pass the risk manager."""
    spec = StrategySpec(
        "steady_scalper", "1.0",
        "Same pullback entries as trend_pullback with a wide stop and a near target, to test what a high win rate costs.",
        "Same as trend_pullback.", "Same as trend_pullback.", "Same as trend_pullback.", "Same as trend_pullback.",
        "2.5 x ATR(14).", "T1 = 0.4R (half), T2 = 0.8R (half).", "Flat at the mandatory exit.", "n/a (research only)",
        "Research only: blended reward:risk is below the 1.5 minimum.", "15 minutes to 2 hours")
    stop_atr, t1_r, t2_r = 2.5, 0.4, 0.8


REGISTRY = {c.spec.name: c for c in (TrendPullback, OrbRetest, FailedBreakout, RangeReversion, VolExpansion,
                                             OrbBreakout, VwapReclaim, GapFade, PriorDayBreak, SteadyScalper)}
BASELINE = BaselineHourlyTrend


def build(names, **overrides) -> list[Strategy]:
    return [REGISTRY[n](**overrides.get(n, {})) for n in names]
