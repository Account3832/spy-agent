"""Indicators and price-action features. Every feature at row i uses only bars <= i.

Timestamp convention: a bar's index is its START time. It is usable only from
start + 5 minutes (its close). One-hour bars are aggregated from the 5-minute bars and
are joined to a 5-minute bar only once the hour has COMPLETED (see add_features).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import calendar as cal
from .config import TZ

BAR_MIN = 5


# ------------------------------------------------------------- primitives

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out = out.where(~((dn == 0) & (up > 0)), 100.0)   # only gains in the window -> RSI 100, not NaN
    return out.where(~((dn == 0) & (up == 0)), 50.0)


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift()
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(h, l, c, n: int = 14) -> pd.Series:
    return true_range(h, l, c).ewm(alpha=1 / n, adjust=False).mean()


def adx(h, l, c, n: int = 14) -> pd.Series:
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    a = atr(h, l, c, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def bollinger(c: pd.Series, n: int = 20, k: float = 2.0):
    m = c.rolling(n).mean()
    s = c.rolling(n).std(ddof=0)
    return m, m + k * s, m - k * s


def macd(c: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(c, fast) - ema(c, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset each session, using typical price x volume. NaN where no volume exists."""
    tp = (df.high + df.low + df.close) / 3
    d = df.index.date
    pv = (tp * df.volume).groupby(d).cumsum()
    v = df.volume.groupby(d).cumsum()
    return (pv / v.replace(0, np.nan))


# ------------------------------------------------------------ hourly bars

def make_hourly(df5: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5-minute bars into hourly bars anchored at 09:30. Index = bar start.
    `available_at` = end of the hour (or the session close), i.e. when the hour is complete."""
    rs = df5.resample("60min", offset="30min", closed="left", label="left")
    h = rs.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
    # An hourly bar is usable only once its WHOLE hour has elapsed (capped at the session close).
    # Using the last bar seen so far would expose a partial hour as if it were complete.
    ends = []
    for st in h.index:
        b = cal.session_bounds(st.date())
        end = st + pd.Timedelta(minutes=60)
        ends.append(min(end, b[1]) if b is not None else end)
    h["available_at"] = pd.DatetimeIndex(ends)
    c = h.close
    h["h_ema20"] = ema(c, 20)
    h["h_slope"] = h["h_ema20"] - h["h_ema20"].shift(3)
    h["h_atr"] = atr(h.high, h.low, c, 14)
    h["h_adx"] = adx(h.high, h.low, c, 14)
    hh, hl = h.high > h.high.shift(), h.low > h.low.shift()
    lh, ll = h.high < h.high.shift(), h.low < h.low.shift()
    h["h_struct"] = (hh & hl).astype(int).rolling(3).sum() - (lh & ll).astype(int).rolling(3).sum()
    h["h_dir"] = np.where((c > h.h_ema20) & (h.h_slope > 0), 1, np.where((c < h.h_ema20) & (h.h_slope < 0), -1, 0))
    return h.rename(columns={"close": "h_close"})[
        ["available_at", "h_close", "h_ema20", "h_slope", "h_atr", "h_adx", "h_struct", "h_dir"]]


def _ns(s: pd.Series) -> pd.Series:
    return pd.Series(pd.DatetimeIndex(s).as_unit("ns"), index=s.index)


# -------------------------------------------------------------- main entry

def add_features(df5: pd.DataFrame) -> pd.DataFrame:
    if df5.index.tz is None:
        raise ValueError("df5 index must be timezone-aware")
    d = df5.copy().sort_index()
    d["date"] = d.index.date
    d["tod"] = d.index.strftime("%H:%M")
    d["bar_close"] = d.index + pd.Timedelta(minutes=BAR_MIN)
    c, h, l = d.close, d.high, d.low

    d["ema9"], d["ema21"], d["ema50"] = ema(c, 9), ema(c, 21), ema(c, 50)
    d["rsi"] = rsi(c)
    d["atr"] = atr(h, l, c)
    d["adx"] = adx(h, l, c)
    d["bb_mid"], d["bb_up"], d["bb_lo"] = bollinger(c)
    d["macd"], d["macd_sig"], d["macd_hist"] = macd(c)
    d["vwap"] = session_vwap(d)
    d["range_atr"] = (h - l) / d["atr"].shift(1)
    d["hi20"] = h.rolling(20).max().shift(1)
    d["lo20"] = l.rolling(20).min().shift(1)
    lr = np.log(c).diff()
    d["rv"] = lr.rolling(78).std() * np.sqrt(78 * 252)

    # relative volume vs the same time of day on prior days (causal)
    if (d.volume > 0).any():
        d["rvol"] = d.volume / d.groupby("tod")["volume"].transform(
            lambda s: s.shift(1).rolling(10, min_periods=3).mean()).replace(0, np.nan)
    else:
        d["rvol"] = np.nan

    # opening range: known only after the 6th bar closes
    bar_no = d.groupby("date").cumcount()
    first = bar_no < 6
    d["or_high"] = h.where(first).groupby(d["date"]).transform("max").where(~first)
    d["or_low"] = l.where(first).groupby(d["date"]).transform("min").where(~first)

    # prior-session levels (from this same data)
    daily = d.groupby("date").agg(h=("high", "max"), l=("low", "min"), c=("close", "last")).shift(1)
    d["pdh"], d["pdl"], d["pdc"] = (d["date"].map(daily[k]) for k in ("h", "l", "c"))

    # one-hour context joined only after each hour has completed
    hf = make_hourly(d[["open", "high", "low", "close", "volume"]])
    left = d[["bar_close"]].copy()
    left["bar_close"] = _ns(left["bar_close"])
    left = left.reset_index(names="ts")
    right = hf.reset_index(drop=True)
    right["available_at"] = _ns(right["available_at"])
    right = right.sort_values("available_at")
    m = pd.merge_asof(left.sort_values("bar_close"), right, left_on="bar_close", right_on="available_at",
                      direction="backward").set_index("ts").reindex(d.index)
    for col in ["h_close", "h_ema20", "h_slope", "h_atr", "h_adx", "h_struct", "h_dir"]:
        d[col] = m[col]
    d["h_dir"] = d["h_dir"].fillna(0).astype(int)

    d["regime"] = np.where((d.adx >= 22) & (d.h_dir != 0), "trend", np.where(d.adx < 18, "range", "transition"))
    med = d["atr"].rolling(78 * 20, min_periods=78).median()
    d["vol_regime"] = np.where(d.atr > 1.25 * med, "expanding", np.where(d.atr < 0.8 * med, "contracting", "normal"))
    return d
