"""Data quality checks. Blocking issues disable signals; warnings are logged."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import calendar as cal

BLOCKING = {"EMPTY", "STALE", "BAD_OHLC", "MISSING_RECENT", "NOT_TZ_AWARE"}


@dataclass
class QualityReport:
    issues: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, code: str, msg: str):
        self.issues.append({"code": code, "msg": msg, "blocking": code in BLOCKING})

    @property
    def blocking(self) -> list[dict]:
        return [i for i in self.issues if i["blocking"]]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def summary(self) -> str:
        return "OK" if self.ok and not self.issues else "; ".join(f"{i['code']}: {i['msg']}" for i in self.issues)


def check_bars(df: pd.DataFrame, now: pd.Timestamp, interval_min: int = 5, max_age_sec: int = 720,
               recent_days: int = 3, volume_expected: bool = False, raw_duplicates: int = 0) -> QualityReport:
    q = QualityReport()
    if df is None or len(df) == 0:
        q.add("EMPTY", "no bars")
        return q
    if df.index.tz is None:
        q.add("NOT_TZ_AWARE", "index has no timezone")
        return q
    q.stats["rows"] = int(len(df))
    q.stats["first"], q.stats["last"] = str(df.index[0]), str(df.index[-1])
    if raw_duplicates:
        q.add("DUPLICATES", f"{raw_duplicates} duplicate timestamps were collapsed")
    if not df.index.is_monotonic_increasing:
        q.add("NON_MONOTONIC", "timestamps not sorted")
    bad = (df.high < df[["open", "close"]].max(axis=1) - 1e-9) | (df.low > df[["open", "close"]].min(axis=1) + 1e-9) \
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if bad.any():
        q.add("BAD_OHLC", f"{int(bad.sum())} bars violate OHLC sanity (first {df.index[bad][0]})")
    if volume_expected and (df.volume <= 0).mean() > 0.5:
        q.add("NO_VOLUME", "volume is mostly zero; VWAP unreliable without a volume proxy")

    # missing bars versus the exchange calendar, checked on the most recent trading days
    days = sorted({d for d in df.index.date if cal.is_trading_day(d)})[-recent_days:]
    missing_total = 0
    for d in days:
        exp = cal.expected_bar_starts(d, interval_min)
        have = df.index[df.index.date == d]
        if d == now.date() and cal.is_open(now):
            exp = exp[exp + pd.Timedelta(minutes=interval_min) <= now]
        miss = exp.difference(have)
        missing_total += len(miss)
        if len(miss) > 3:
            q.add("MISSING_RECENT", f"{d}: {len(miss)} of {len(exp)} expected bars missing")
        elif len(miss) > 0:
            q.add("MISSING_FEW", f"{d}: {len(miss)} bars missing")
    q.stats["missing_recent"] = missing_total

    # staleness only matters while the market is open
    if cal.is_open(now):
        age = (now - (df.index[-1] + pd.Timedelta(minutes=interval_min))).total_seconds()
        q.stats["last_bar_age_sec"] = age
        if age > max_age_sec:
            q.add("STALE", f"latest completed bar is {age:.0f}s old (limit {max_age_sec}s)")
    return q
