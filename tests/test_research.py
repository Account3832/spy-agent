"""Indicators, 5m/1h alignment, no-look-ahead, strategies, backtest assumptions. Data is SIMULATED."""
import numpy as np
import pandas as pd

from spxai import features as F
from spxai.backtest import (boot_ci, chrono_split, edge_verdict, metrics, resolve_trade, run_research, simulate,
                            walk_forward_folds)
from spxai.config import Settings
from spxai.features import add_features
from spxai.strategies import BASELINE, REGISTRY
from tests.helpers import synth

S = Settings()


# ------------------------------------------------------------- indicators

def test_indicator_known_values():
    c = pd.Series(np.arange(1.0, 60.0))
    assert abs(F.ema(pd.Series([5.0] * 30), 9).iloc[-1] - 5.0) < 1e-12
    assert F.rsi(c).iloc[-1] == 100.0                          # strictly rising => RSI 100
    h, l = c + 1, c - 1
    assert abs(F.atr(h, l, c).iloc[-1] - 2.0) < 0.05           # constant range 2, tiny gap contribution
    m, up, lo = F.bollinger(pd.Series([10.0] * 30))
    assert up.iloc[-1] == lo.iloc[-1] == 10.0
    a = F.adx(h, l, c).dropna()
    assert ((a >= 0) & (a <= 100)).all()


def test_session_vwap_resets_each_day_and_matches_manual():
    d = synth(3)
    v = F.session_vwap(d)
    day2 = d[d.index.date == d.index[-1].date()]
    tp = (day2.high + day2.low + day2.close) / 3
    manual = (tp * day2.volume).cumsum() / day2.volume.cumsum()
    assert np.allclose(v.loc[day2.index].values, manual.values)
    first = d.iloc[0]
    assert abs(v.iloc[0] - (first.high + first.low + first.close) / 3) < 1e-9


def test_opening_range_unavailable_until_after_sixth_bar():
    f = add_features(synth(4))
    day = f[f.date == f.date.iloc[-1]]
    assert day.or_high.iloc[:6].isna().all() and day.or_high.iloc[6:].notna().all()
    assert day.or_high.iloc[6] == day.high.iloc[:6].max() and day.or_low.iloc[6] == day.low.iloc[:6].min()


def test_prior_day_levels_come_from_previous_session_only():
    f = add_features(synth(4))
    d_last, d_prev = sorted(set(f.date))[-1], sorted(set(f.date))[-2]
    prev = f[f.date == d_prev]
    last = f[f.date == d_last]
    assert (last.pdh == prev.high.max()).all() and (last.pdl == prev.low.min()).all() and (last.pdc == prev.close.iloc[-1]).all()


# --------------------------------------------- 5m / 1h alignment, look-ahead

def test_hourly_bars_built_from_5m_and_complete_only_when_joined():
    raw = synth(6)
    h = F.make_hourly(raw)
    first = raw[raw.index.date == raw.index[0].date()]
    assert h.index[0] == first.index[0]                       # buckets anchored at 09:30
    assert h.available_at.iloc[0] == first.index[0] + pd.Timedelta(minutes=60)  # complete at 10:30
    f = add_features(raw)
    # the 09:30-10:30 hourly bar must NOT be visible before 10:30
    # the 10:25 bar closes at 10:30, the instant the first hour completes, so it MAY see it; earlier bars may not
    early = f.loc[f.index < first.index[0].normalize() + pd.Timedelta(hours=10, minutes=25)]
    first_day_early = early[early.date == first.index[0].date()]
    assert first_day_early.h_close.isna().all()


def test_no_lookahead_features_identical_when_future_is_removed():
    raw = synth(25)
    full = add_features(raw)
    for cut in (500, 1200, 1700):
        part = add_features(raw.iloc[:cut])
        a, b = full.iloc[:cut], part
        for col in ["ema21", "rsi", "atr", "adx", "vwap", "or_high", "pdh", "h_close", "h_dir", "h_adx", "regime", "rvol"]:
            x, y = a[col].values, b[col].values
            both = ~(pd.isna(x) | pd.isna(y))
            assert (pd.isna(x) == pd.isna(y)).all(), f"{col} availability changed with future data"
            if x.dtype.kind in "fi":
                assert np.allclose(x[both].astype(float), y[both].astype(float), atol=1e-9), col
            else:
                assert (x[both] == y[both]).all(), col


def test_no_lookahead_strategy_signals_identical_when_future_is_removed():
    raw = synth(30, seed=3)
    full = add_features(raw)
    cut = 1500
    part = add_features(raw.iloc[:cut])
    for name, cls in REGISTRY.items():
        a = cls().signals(full).iloc[:cut]["dir"].values
        b = cls().signals(part)["dir"].values
        assert (a == b).all(), f"{name} signals depend on future bars"


# ------------------------------------------------------ simulation rules

def _bars(rows):
    n = len(rows)
    o, h, l, c = (np.array([r[i] for r in rows], dtype=float) for i in range(4))
    tod = np.array([f"{9 + (30 + 5 * i) // 60}:{(30 + 5 * i) % 60:02d}".replace(":0", ":0") for i in range(n)])
    tod = np.array([f"{9 + (30 + 5 * i) // 60:02d}:{(30 + 5 * i) % 60:02d}" for i in range(n)])
    date = np.array(["d"] * n, dtype=object)
    return o, h, l, c, tod, date


def test_stop_wins_when_stop_and_target_in_same_bar():
    # long entry 100, risk 1 -> stop 99, T1 101.5. Bar 1 spans both.
    o, h, l, c, tod, date = _bars([(100, 100.2, 99.9, 100), (100, 102, 98.5, 100), (100, 100, 100, 100)])
    r = resolve_trade(o, h, l, c, tod, date, 0, 1, 100.0, 100.0, 1.0, 99.0, 101.5, 103.0, 0.0, "15:45", 36)
    assert r["reasons"][1] == "stop" and r["gross"] < 0


def test_gap_through_stop_fills_at_open_not_at_stop():
    o, h, l, c, tod, date = _bars([(100, 100.2, 99.9, 100), (97, 97.5, 96.5, 97)])
    r = resolve_trade(o, h, l, c, tod, date, 0, 1, 100.0, 100.0, 1.0, 99.0, 101.5, 103.0, 0.0, "15:45", 36)
    assert abs(r["net"] - (-3.0)) < 1e-9  # filled at 97, worse than the 99 stop


def test_t2_cannot_fill_on_the_same_bar_as_t1_and_stop_moves_to_breakeven_next_bar():
    # bar0 entry, bar1 reaches T1 AND T2 levels (only T1 may fill), bar2 drops to entry (breakeven stop)
    o, h, l, c, tod, date = _bars([(100, 100.2, 99.9, 100), (100.5, 104, 100.4, 103), (103, 103.1, 99.5, 100)])
    r = resolve_trade(o, h, l, c, tod, date, 0, 1, 100.0, 100.0, 1.0, 99.0, 101.5, 103.0, 0.0, "15:45", 36)
    assert r["reasons"][1] == "T1" and r["reasons"][2] == "BE-stop"
    assert abs(r["net"] - 0.5 * 1.5) < 1e-9  # half at +1.5, half scratched at 0


def test_target_requires_trade_through_of_slippage_buffer():
    o, h, l, c, tod, date = _bars([(100, 100.2, 99.9, 100), (100, 101.5, 100, 101), (101, 101.0, 101.0, 101)])
    r = resolve_trade(o, h, l, c, tod, date, 0, 1, 100.0, 100.0, 1.0, 99.0, 101.5, 103.0, 0.05, "15:45", 36, data_end_closes=False)
    assert r["halves_open"][1]  # touched 101.5 exactly but not through it by the 0.05 buffer


def test_time_exit_uses_next_open_and_short_side_mirrors():
    o, h, l, c, tod, date = _bars([(100, 100.1, 99.9, 100)] + [(100, 100.3, 99.8, 100)] * 3)
    tod[3] = "15:45"
    r = resolve_trade(o, h, l, c, tod, date, 0, -1, 100.0, 100.0, 1.0, 101.0, 98.5, 97.0, 0.0, "15:45", 36)
    assert r["reasons"] == {1: "time", 2: "time"} and r["last_j"] == 3


def test_costs_reduce_net_below_gross():
    d = add_features(synth(40, seed=2))
    t = simulate(d, REGISTRY["failed_breakout"](), S)
    assert len(t) > 10 and (t.net_R <= t.gross_R + 1e-9).all() and t.net_R.mean() < t.gross_R.mean()
    zero = Settings(slippage_usd=0.0)
    t0 = simulate(d, REGISTRY["failed_breakout"](), zero)
    assert abs(t0.net_R.mean() - t0.gross_R.mean()) < 1e-9


def test_entry_is_next_bar_open_after_signal_and_one_position_at_a_time():
    d = add_features(synth(40, seed=2))
    t = simulate(d, REGISTRY["failed_breakout"](), S)
    assert (t.entry_time > t.signal_time).all()
    assert (t.entry_time - t.signal_time == pd.Timedelta(minutes=5)).all()
    srt = t.sort_values("entry_time")
    assert (srt.entry_time.values[1:] >= srt.exit_time.values[:-1]).all()      # no overlap
    assert t.groupby("date").size().max() <= S.max_trades_per_day
    assert (t.exit_time.dt.strftime("%H:%M") <= "15:55").all()


# ----------------------------------------------- metrics, splits, verdicts

def test_metrics_known_values():
    t = pd.DataFrame({"net_R": [1, -1, -1, 2, -1, -1, -1, 1.0], "gross_R": [1.1, -.9, -.9, 2.1, -.9, -.9, -.9, 1.1]})
    m = metrics(t)
    assert m["trades"] == 8 and m["win_rate"] == 0.375 and m["longest_losing_streak"] == 3
    assert m["profit_factor"] == round(4 / 5, 2) and abs(m["net_expectancy_R"] - (-0.125)) < 1e-9
    assert m["max_drawdown_R"] == 3.0 and m["gross_expectancy_R"] > m["net_expectancy_R"]


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean():
    r = np.random.default_rng(0).normal(0.1, 1, 200)
    a, b = boot_ci(r), boot_ci(r)
    assert a == b and a[0] < r.mean() < a[1]


def test_chronological_split_and_walk_forward_never_overlap():
    dates = list(range(100))
    sp = chrono_split(dates)
    assert max(sp["train"]) < min(sp["val"]) and max(sp["val"]) < min(sp["oos"])
    assert len(sp["train"]) + len(sp["val"]) + len(sp["oos"]) == 100
    for f in walk_forward_folds(dates, 10, 20):
        assert int(f["train_end"]) < int(f["test_start"])


def test_verdict_logic():
    ok = {"trades": 150, "net_expectancy_R": 0.2, "ci_low": 0.05, "ci_high": 0.35}
    assert edge_verdict({"trades": 20}, 1.0, 0.0, 100)[0] == "INSUFFICIENT_SAMPLE"
    assert edge_verdict({"trades": 150, "net_expectancy_R": -0.2, "ci_low": -0.3, "ci_high": -0.1}, .5, 0, 100)[0] == "FAILED_OOS"
    assert edge_verdict({"trades": 150, "net_expectancy_R": 0.1, "ci_low": -0.1, "ci_high": 0.3}, .8, 0, 100)[0] == "NOT_DEMONSTRATED"
    assert edge_verdict(ok, 0.8, 0.0, 100)[0] == "UNDERLYING_EDGE_SUPPORTED"
    assert edge_verdict(ok, 0.4, 0.0, 100)[0] == "NOT_DEMONSTRATED"          # unstable across folds
    assert edge_verdict(ok, 0.8, 0.5, 100)[0] == "NOT_DEMONSTRATED"          # does not beat baseline


def test_random_walk_never_yields_an_edge_claim_and_options_research_is_unavailable():
    rep = run_research(add_features(synth(45, seed=5)), S, grid=False)
    assert all(r["verdict"] != "UNDERLYING_EDGE_SUPPORTED" for r in rep["strategies"].values())
    assert rep["options_profitability_research"]["status"] == "UNAVAILABLE"
    assert "NOT option profitability" in rep["report_type"]
    assert rep["multiple_testing"]["ci_level_used"] > 0.95
    assert any("Only 45 trading days" in w for w in rep["warnings"])


def test_report_declares_oos_once_and_sensitivity_uses_train_val_only():
    rep = run_research(add_features(synth(60, seed=6)), S, strategy_names=["failed_breakout"])
    r = rep["strategies"]["failed_breakout"]
    assert "out_of_sample_reported_once" in r and "sensitivity_train_val" in r and "ablation_train_val" in r
    assert len(r["sensitivity_train_val"]["cells"]) == 9
    assert {"with_default_filters", "add_htf", "add_vwap"} <= set(r["ablation_train_val"])
    assert rep["baseline_hourly_trend"]["out_of_sample"] is not None
