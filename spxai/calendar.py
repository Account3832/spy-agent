"""NYSE sessions, holidays, early closes, and user-maintained event blackouts.

Holiday and early-close tables are hard-coded for 2026-2027 and MUST be re-verified
against the official NYSE calendar each year. Event dates live in config/events.yaml
and are user-maintained; nothing here is fetched live.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .config import ROOT, TZ

D = dt.date
HOLIDAYS = {
    # 2026
    D(2026, 1, 1), D(2026, 1, 19), D(2026, 2, 16), D(2026, 4, 3), D(2026, 5, 25),
    D(2026, 6, 19), D(2026, 7, 3), D(2026, 9, 7), D(2026, 11, 26), D(2026, 12, 25),
    # 2027
    D(2027, 1, 1), D(2027, 1, 18), D(2027, 2, 15), D(2027, 3, 26), D(2027, 5, 31),
    D(2027, 6, 18), D(2027, 7, 5), D(2027, 9, 6), D(2027, 11, 25), D(2027, 12, 24),
}
EARLY_CLOSE = {D(2026, 11, 27), D(2026, 12, 24), D(2027, 11, 26)}  # 13:00 ET


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
