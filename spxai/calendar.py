"""NYSE sessions, holidays, early closes, and user-maintained event blackouts.

Holidays and early closes are generated from NYSE rules for 2015-2030 (plus two one-off closures)
and MUST be re-verified against the official NYSE calendar each year. Event dates live in config/events.yaml
and are user-maintained; nothing here is fetched live.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .config import ROOT, TZ

D = dt.date


def _nth_weekday(y: int, m: int, wd: int, n: int) -> dt.date:
    d = D(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, wd: int) -> dt.date:
    d = D(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _easter(y: int) -> dt.date:  # anonymous Gregorian algorithm
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, da = divmod(h + l - 7 * m + 114, 31)
    return D(y, mo, da + 1)


def _observed(d: dt.date, sat_to_friday: bool = True) -> dt.date | None:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1) if sat_to_friday else None
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def _build(y0: int = 2015, y1: int = 2030):
    hol, early = set(), set()
    for y in range(y0, y1 + 1):
        fixed = [_observed(D(y, 1, 1), sat_to_friday=False), _observed(D(y, 7, 4)), _observed(D(y, 12, 25))]
        if y >= 2022:
            fixed.append(_observed(D(y, 6, 19)))
        fixed += [_nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3), _easter(y) - dt.timedelta(days=2),
                  _last_weekday(y, 5, 0), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4)]
        hol.update(h for h in fixed if h)
        tg = _nth_weekday(y, 11, 3, 4)
        early.add(tg + dt.timedelta(days=1))                       # day after Thanksgiving
        j3 = D(y, 7, 3)
        if j3.weekday() <= 3:                                        # Mon-Thu before July 4
            early.add(j3)
        d24 = D(y, 12, 24)
        if d24.weekday() < 5:
            early.add(d24)
    hol.update({D(2018, 12, 5), D(2025, 1, 9)})                      # national days of mourning
    early -= hol
    return hol, early


HOLIDAYS, EARLY_CLOSE = _build()   # EARLY_CLOSE = 13:00 ET. Rule-based: re-verify against the NYSE calendar each year.


def _ts(d: dt.date, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{d.isoformat()} {hhmm}", tz=TZ)


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def session_bounds(d: dt.date):
    if not is_trading_day(d):
        return None
    return _ts(d, "09:30"), _ts(d, "13:00" if d in EARLY_CLOSE else "16:00")


def expected_bar_starts(d: dt.date, minutes: int = 5) -> pd.DatetimeIndex:
    b = session_bounds(d)
    if b is None:
        return pd.DatetimeIndex([], tz=TZ)
    return pd.date_range(b[0], b[1], freq=f"{minutes}min", inclusive="left")


def is_open(now: pd.Timestamp) -> bool:
    b = session_bounds(now.date())
    return b is not None and b[0] <= now < b[1]


def minutes_to_close(now: pd.Timestamp) -> float:
    b = session_bounds(now.date())
    if b is None:
        return 0.0
    return max(0.0, (b[1] - now).total_seconds() / 60.0)


def mandatory_exit_ts(d: dt.date, hhmm: str) -> pd.Timestamp:
    """Mandatory exit, capped 15 minutes before an early close."""
    b = session_bounds(d)
    t = _ts(d, hhmm)
    if b is not None and t > b[1] - pd.Timedelta(minutes=15):
        return b[1] - pd.Timedelta(minutes=15)
    return t


def load_events(path=None) -> list[dict]:
    import yaml
    p = path or (ROOT / "config" / "events.yaml")
    try:
        data = yaml.safe_load(open(p)) or {}
    except FileNotFoundError:
        return []
    return list(data.get("events", []))


def blackout_reason(now: pd.Timestamp, events: list[dict] | None = None) -> str | None:
    """Return a reason string if `now` falls in a blackout. Whole-day by default."""
    for e in events if events is not None else load_events():
        if str(e.get("date")) != now.date().isoformat():
            continue
        if e.get("blackout", "whole_day") == "whole_day":
            return f"event blackout: {e.get('name', 'event')} (verified={e.get('verified', False)})"
        t = e.get("time")
        if t:
            c = _ts(now.date(), t)
            before = pd.Timedelta(minutes=int(e.get("minutes_before", 30)))
            after = pd.Timedelta(minutes=int(e.get("minutes_after", 60)))
            if c - before <= now <= c + after:
                return f"event blackout window: {e.get('name', 'event')} at {t}"
    return None
