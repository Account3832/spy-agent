"""Providers, cost guard, labelling, store, quality, calendar. Fixtures are SIMULATED."""
import os
import unittest

import numpy as np
import pandas as pd

from spxai import calendar as cal
from spxai.config import Settings
from spxai.providers import (REGISTRY, Capability, CircuitBreaker, CircuitOpen, CostViolation, DataError, NO_OPTIONS_MSG,
                             assert_free, cost_violations, data_status, get_capability, normalize_intraday,
                             parse_alpaca_bars, parse_alpaca_snapshots, parse_occ, with_retry)
from spxai.quality import check_bars
from spxai.store import append_jsonl, load_bars, read_jsonl, save_bars
from tests.helpers import raises, synth, tmpdir

NY = "America/New_York"
S = Settings()


def test_default_registry_respects_zero_budget():
    assert cost_violations(S) == []


def test_paid_provider_enabled_is_flagged():
    paid = Capability("x", "options", 99.0, True, "realtime_licensed", "no", "yes", "yes", "n/a", "key", "allowed", True, "official docs")
    assert cost_violations(S, [paid])


def test_assert_free_blocks_paid_and_prohibited():
    assert raises(CostViolation, assert_free, "opra_via_alpaca", S)
    assert raises(CostViolation, assert_free, "intrinio_options_15min", S)
    assert raises(CostViolation, assert_free, "cboe_delayed_web_pages", S)  # automated use prohibited
    assert assert_free("alpaca_equities_iex", S).monthly_cost_usd == 0


def test_no_paid_provider_enabled_by_default():
    assert all(c.monthly_cost_usd == 0 and not c.requires_paid_subscription for c in REGISTRY if c.enabled)


def test_options_unavailable_message_is_exact():
    assert NO_OPTIONS_MSG == "REAL-TIME SPY OPTIONS QUOTES UNAVAILABLE UNDER THE CURRENT $0 DATA REQUIREMENT."


def test_live_vs_delayed_labelling():
    now = pd.Timestamp("2026-10-12 10:00", tz=NY)
    fresh = now - pd.Timedelta(seconds=60)
    assert data_status(get_capability("alpaca_equities_iex"), fresh, now) == "live"
    assert data_status(get_capability("yahoo_finance_unofficial"), fresh, now) == "delayed"  # never 'live'
    assert data_status(get_capability("alpaca_equities_iex"), now - pd.Timedelta(hours=2), now) == "historical"
    assert data_status(get_capability("alpaca_equities_iex"), fresh, now, simulated=True) == "simulated"
    assert data_status(get_capability("alpaca_equities_iex"), fresh, now, historical=True) == "historical"


def test_normalize_drops_incomplete_bar_dups_and_converts_tz():
    idx = pd.to_datetime(["2026-10-12 13:30", "2026-10-12 13:35", "2026-10-12 13:35", "2026-10-12 13:40"], utc=True)
    raw = pd.DataFrame({"Open": [1, 2, 2, 3.], "High": [2, 3, 3, 4.], "Low": [1, 2, 2, 3.], "Close": [2, 3, 3.5, 4.],
                        "Volume": [10, 5, 6, 7.]}, index=idx)
    now = pd.Timestamp("2026-10-12 09:41", tz=NY)  # 13:40 UTC bar closes 09:45 ET: still forming
    out = normalize_intraday(raw, now)
    assert len(out) == 2 and str(out.index.tz) == NY
    assert out.close.iloc[-1] == 3.5  # duplicate collapsed, newest wins
    assert out.index[-1] + pd.Timedelta(minutes=5) <= now


def test_parse_alpaca_bars_uses_start_timestamps():  # SIMULATED payload
    rows = [{"t": "2026-10-12T13:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}]
    df = parse_alpaca_bars(rows, pd.Timestamp("2026-10-12 12:00", tz=NY))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] == pd.Timestamp("2026-10-12 09:30", tz=NY)
    assert raises(DataError, parse_alpaca_bars, [])


def test_alpaca_option_quotes_are_always_indicative_never_executable():  # SIMULATED payload
    payload = {"snapshots": {"SPY261013C00680000": {"latestQuote": {"bp": 1.0, "ap": 1.1, "t": "2026-10-12T14:00:00Z"},
                                                    "greeks": {"delta": 0.4}, "impliedVolatility": 0.2}}}
    df = parse_alpaca_snapshots(payload, "SPY")
    assert set(df.quote_class) == {"indicative"} and df.strike.iloc[0] == 680.0 and df.right.iloc[0] == "call"


def test_parse_occ():
    o = parse_occ("SPY261013P00675500")
    assert o["root"] == "SPY" and o["right"] == "put" and o["strike"] == 675.5 and str(o["expiry"]) == "2026-10-13"
    assert parse_occ("garbage") is None


def test_retry_backoff_and_circuit_breaker():
    sleeps, calls = [], {"n": 0}

    def bad():
        calls["n"] += 1
        raise RuntimeError("down")
    t = {"v": 0.0}
    br = CircuitBreaker(threshold=3, reset_after=100, clock=lambda: t["v"])
    assert raises(DataError, with_retry, bad, tries=3, base=1.0, breaker=br, sleep=sleeps.append)
    assert sleeps == [1.0, 2.0] and calls["n"] == 3  # exponential backoff
    assert raises(CircuitOpen, with_retry, bad, breaker=br, sleep=sleeps.append)  # open: not hammered
    assert calls["n"] == 3
    t["v"] = 101.0
    assert br.allow()  # half-open probe after cool-down
    assert with_retry(lambda: 7, breaker=br) == 7 and br.state == "closed"


def test_live_provider_connectivity_skipped_without_opt_in():
    if os.environ.get("SPXAI_RUN_LIVE") != "1":
        raise unittest.SkipTest("live network test; set SPXAI_RUN_LIVE=1 to run")
    from spxai.providers import YahooProvider
    df = YahooProvider().intraday("SPY", "5m", "5d")
    assert {"open", "high", "low", "close", "volume"} <= set(df.columns) and len(df) > 0


# ------------------------------------------------------------------ store

def test_store_merges_dedupes_and_is_idempotent():
    base, raw = tmpdir(), synth(5)
    a = save_bars("SPY", "5m", raw.iloc[:200], base)
    b = save_bars("SPY", "5m", raw.iloc[100:300], base)  # overlap
    c = save_bars("SPY", "5m", raw.iloc[100:300], base)  # re-ingest same data
    assert a["added"] == 200 and b["added"] == 100 and c["added"] == 0
    got = load_bars("SPY", "5m", base)
    assert len(got) == 300 and got.index.is_unique and str(got.index.tz) == NY


def test_migration_safety_history_never_shrinks_or_resets():
    base, raw = tmpdir(), synth(5)
    save_bars("SPY", "5m", raw, base)
    save_bars("SPY", "5m", raw.iloc[:0], base)          # empty fetch must not wipe history
    assert len(load_bars("SPY", "5m", base)) == len(raw)
    assert (base / "bars" / "SPY_5m.csv.bak").exists()  # rolling backup written


def test_jsonl_tolerates_torn_last_line():
    p = tmpdir() / "x.jsonl"
    append_jsonl(p, {"a": 1})
    with open(p, "a") as f:
        f.write('{"a": 2')  # crash mid-write
    assert read_jsonl(p) == [{"a": 1}]


# ---------------------------------------------------------------- quality

def test_quality_ok_on_clean_data_and_flags_missing_stale_badohlc():
    raw = synth(6)
    now = raw.index[-1] + pd.Timedelta(minutes=5, seconds=30)
    assert check_bars(raw, now).ok
    holes = raw.drop(raw.index[-40:-30])
    q = check_bars(holes, now)
    assert not q.ok and any(i["code"] == "MISSING_RECENT" for i in q.issues)
    bad = raw.copy()
    bad.iloc[-1, bad.columns.get_loc("high")] = bad.iloc[-1].low - 1
    assert any(i["code"] == "BAD_OHLC" for i in check_bars(bad, now).issues)
    # stale: market open but last bar is old
    day = cal.expected_bar_starts(pd.Timestamp("2026-10-12").date())
    old = synth(3).copy()
    later = pd.Timestamp("2026-10-12 11:00", tz=NY)
    qs = check_bars(raw.loc[raw.index.date == raw.index[-1].date()], later)
    assert any(i["code"] == "STALE" for i in qs.issues) or not cal.is_open(later)


def test_quality_empty_and_naive_timestamps_block():
    now = pd.Timestamp("2026-10-12 10:00", tz=NY)
    assert not check_bars(pd.DataFrame(), now).ok
    naive = synth(2)
    naive.index = naive.index.tz_localize(None)
    assert any(i["code"] == "NOT_TZ_AWARE" for i in check_bars(naive, now).issues)


def test_stale_not_flagged_when_market_closed():
    raw = synth(4)
    closed_now = raw.index[-1].normalize() + pd.Timedelta(hours=20)
    assert not any(i["code"] == "STALE" for i in check_bars(raw, closed_now).issues)


# --------------------------------------------------------------- calendar

def test_calendar_holidays_early_close_and_expected_bars():
    import datetime as dt
    assert not cal.is_trading_day(dt.date(2026, 11, 26))      # Thanksgiving
    assert not cal.is_trading_day(dt.date(2026, 10, 10))      # Saturday
    assert len(cal.expected_bar_starts(dt.date(2026, 10, 12))) == 78
    assert len(cal.expected_bar_starts(dt.date(2026, 11, 27))) == 42  # early close 13:00
    assert len(cal.expected_bar_starts(dt.date(2026, 11, 26))) == 0
    assert cal.mandatory_exit_ts(dt.date(2026, 11, 27), "15:45").strftime("%H:%M") == "12:45"  # capped on early close
    assert cal.mandatory_exit_ts(dt.date(2026, 10, 12), "15:45").strftime("%H:%M") == "15:45"


def test_event_blackout_whole_day_and_window():
    now = pd.Timestamp("2026-10-28 11:00", tz=NY)
    assert cal.blackout_reason(now, [{"date": "2026-10-28", "name": "FOMC"}])
    win = [{"date": "2026-10-28", "name": "CPI", "blackout": "window", "time": "08:30", "minutes_before": 30, "minutes_after": 60}]
    assert cal.blackout_reason(pd.Timestamp("2026-10-28 09:00", tz=NY), win)
    assert cal.blackout_reason(pd.Timestamp("2026-10-28 11:00", tz=NY), win) is None
    assert cal.blackout_reason(now, []) is None
