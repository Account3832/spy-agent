"""Shared fixtures. ALL market data here is SIMULATED (seeded random walks), never real."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from spxai import calendar as cal
from spxai.agents import Ctx
from spxai.config import Settings
from spxai.features import add_features
from spxai.providers import get_capability
from spxai.quality import check_bars
from spxai.strategies import REGISTRY


def synth(days=40, start="2026-07-06", seed=1, drift=0.0, vol=0.08, base=600.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx, d = [], pd.Timestamp(start).date()
    while len({i.date() for i in idx}) < days:
        if cal.is_trading_day(d):
            idx += list(cal.expected_bar_starts(d))
        d = (pd.Timestamp(d) + pd.Timedelta(days=1)).date()
    idx = pd.DatetimeIndex(idx)
    n = len(idx)
    c = base + np.cumsum(rng.normal(drift, vol, n))
    o = np.r_[c[0], c[:-1]] + rng.normal(0, vol / 6, n)
    hi = np.maximum(o, c) + abs(rng.normal(0, vol / 2, n))
    lo = np.minimum(o, c) - abs(rng.normal(0, vol / 2, n))
    v = rng.integers(1e5, 5e5, n).astype(float)
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": v}, index=idx)


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp())


def find_signal(d: pd.DataFrame, name="trend_pullback", last=True):
    sg = REGISTRY[name]().signals(d)
    ix = sg.index[sg.dir.ne(0).values]
    return ix[-1] if last else ix[0]


def make_ctx(raw: pd.DataFrame, t: pd.Timestamp, s: Settings | None = None, **kw) -> Ctx:
    """Context as of the close of the bar that STARTS at t, using only data up to t."""
    s = s or Settings()
    feats = add_features(raw.loc[:t])
    now = t + pd.Timedelta(minutes=5, seconds=20)
    q = check_bars(raw.loc[:t], now, max_age_sec=s.max_bar_age_sec)
    args = dict(s=s, now=now, df=feats, source="simulated", cap=get_capability("alpaca_equities_iex"),
                data_status="simulated", quality=q, events=[])
    args.update(kw)
    return Ctx(**args)


def raises(exc, fn, *a, **k) -> bool:
    try:
        fn(*a, **k)
    except exc:
        return True
    return False
