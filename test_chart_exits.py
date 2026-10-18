import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from spxai import levels as L
from spxai.agents import AdversarialReviewer, _apply_chart_plan
from spxai.backtest import compare_exits, run_research, simulate
from spxai.config import Settings, load_settings
from spxai.features import add_features
from spxai.monitor import scan, signal_text
from spxai.paper import PaperLedger
from spxai.strategies import REGISTRY
from tests.helpers import make_ctx, synth, tmpdir

NY = "America/New_York"


def Z(lo, hi, strength, role):
    return {"low": lo, "high": hi, "mid": (lo + hi) / 2, "strength": strength, "role": role, "touches": 3}


A = {"ok": True, "zones": [Z(98, 98.8, "Moderate", "support"), Z(106, 107, "Strong", "resistance"), Z(111, 112, "Moderate", "resistance"), Z(115, 116, "Weak", "resistance")]}

def test_structure_plan_long_uses_zones():
    p = L.structure_plan(A, 100.0, 1.5, True)
    assert p["ok"] and abs(p["stop"] - 97.625) < 1e-9 and abs(p["t1"] - 105.85) < 1e-9 and abs(p["t2"] - 110.85) < 1e-9
    assert p["stop"] < 100 < p["t1"] < p["t2"] and p["t1_r"] >= 1.5


def test_structure_plan_passes_when_no_room_or_stop_too_wide_or_no_support():
    assert "needs 1.5R of room" in L.structure_plan(A, 104.0, 1.5, True)["text"] or "too far" in L.structure_plan(A, 104.0, 1.5, True)["text"]
    near = {"ok": True, "zones": [Z(98, 98.8, "Moderate", "support"), Z(101.5, 102, "Strong", "resistance")]}
    r = L.structure_plan(near, 100.0, 1.5, True)
    assert not r["ok"] and "room" in r["fail"]
    assert not L.structure_plan(A, 100.0, 0.3, True)["ok"]                         # support 2R+ away vs a tiny ATR -> stop too wide
    assert "no support zone" in L.structure_plan({"ok": True, "zones": [Z(106, 107, "Strong", "resistance")]}, 100.0, 1.5, True)["fail"]
    assert not L.structure_plan(None, 100.0, 1.5, True)["ok"]


def test_structure_plan_ignores_weak_zones_and_clear_sky_defaults():
    weak = {"ok": True, "zones": [Z(98, 98.8, "Moderate", "support"), Z(103, 104, "Weak", "resistance")]}
    p = L.structure_plan(weak, 100.0, 1.5, True)
    assert p["ok"] and abs(p["t1_r"] - 1.5) < 1e-9 and abs(p["t2_r"] - 3.0) < 1e-9


def test_structure_plan_short_mirrors():
    a = {"ok": True, "zones": [Z(101.2, 102, "Strong", "resistance"), Z(94, 95, "Strong", "support"), Z(90, 91, "Moderate", "support")]}
    p = L.structure_plan(a, 100.0, 1.5, False)
    assert p["ok"] and p["stop"] > 100 > p["t1"] > p["t2"]


def test_trend_break_warning_only_for_matching_direction():
    a = {"ok": True, "timeframes": [{"label": "Intermediate", "tf": "1h", "direction": "UP", "state": "broken", "invalidation": 95.0},
                                    {"label": "Minor", "tf": "15m", "direction": "UP", "state": "broken", "invalidation": 99.0}]}
    assert "1h uptrend structure just broke" in L.trend_break_warning(a, long=True)
    assert L.trend_break_warning(a, long=False) == "" and L.trend_break_warning(None) == ""
    a["timeframes"][0]["state"] = "intact"
    assert L.trend_break_warning(a, long=True) == ""            # the minor scale alone does not trigger it



RAW = synth(60, seed=3)
FEATS = add_features(RAW)


def test_stock_engine_at_matches_truncated_history():
    """Causality with the stock calendar: hours anchored at 09:30, daily candle capped at the 16:00 close."""
    eng = L.Engine(RAW, L.STOCK_TFS, offset_min=30, session_end="16:00")
    for cut in (78 * 30 + 10, 78 * 45 + 55):
        t = RAW.index[cut - 1] + pd.Timedelta(minutes=5)
        x, y = eng.at(t, full=False), L.analyze(RAW.iloc[:cut], L.STOCK_TFS, 30, "16:00")
        assert [(round(z["low"], 6), round(z["high"], 6), z["strength"]) for z in x["zones"]] == [(round(z["low"], 6), round(z["high"], 6), z["strength"]) for z in y["zones"]]
        assert x["alignment"] == y["alignment"]
        assert [r["direction"] for r in x["timeframes"]] == [r["direction"] for r in y["timeframes"]]


def test_prior_day_is_the_last_complete_session():
    eng = L.Engine(RAW, L.STOCK_TFS, offset_min=30, session_end="16:00")
    days = sorted(set(RAW.index.date))
    t = RAW[RAW.index.date == days[20]].index[10] + pd.Timedelta(minutes=5)           # mid-morning on session 21
    tt = np.datetime64(t.tz_convert("UTC").tz_localize(None), "ns")
    kd = int(np.searchsorted(eng.d_av, tt, side="right"))
    assert kd == 20 and eng.daily.index[kd - 1].date() == days[19]


def test_simulate_chart_mode_passes_trades_and_covers_both_sides():
    eng = L.Engine(RAW, L.STOCK_TFS, offset_min=30, session_end="16:00")
    s = Settings()
    skips = []
    atr_t = simulate(FEATS, REGISTRY["trend_pullback"](), s)
    ch_t = simulate(FEATS, REGISTRY["trend_pullback"](), s, engine=eng, exit_mode="chart", skips=skips)
    assert len(atr_t) > 0 and len(skips) > 0 and all(r["reason"] for r in skips)
    if len(ch_t):
        assert (ch_t.risk > 0).all()


def test_compare_exits_is_paired_and_reports_skips():
    eng = L.Engine(RAW, L.STOCK_TFS, offset_min=30, session_end="16:00")
    s = Settings()
    ce = compare_exits(FEATS, s, list(s.enabled_strategies), eng)
    assert ce["verdict"] in ("INSUFFICIENT_SAMPLE", "CHART_EXITS_BETTER", "CHART_EXITS_WORSE", "NOT_DEMONSTRATED")
    for n, v in ce["per_strategy"].items():
        a = v["all_sample"]
        if a.get("trades"):
            assert a["trades"] == a["with_chart_plan"] + a["skipped_no_clean_plan"]


def test_run_research_includes_chart_exit_section_only_with_engine():
    s = Settings()
    assert run_research(FEATS, s, grid=False)["chart_exits"] is None
    rep = run_research(FEATS, s, grid=False, engine=L.Engine(RAW, L.STOCK_TFS, offset_min=30, session_end="16:00"))
    assert rep["chart_exits"]["pooled_out_of_sample"] is not None


def test_exit_mode_is_validated_and_defaults_to_atr():
    os.environ["SPXAI_EXIT_MODE"] = "banana"
    try:
        try:
            load_settings()
            assert False
        except ValueError:
            pass
    finally:
        os.environ.pop("SPXAI_EXIT_MODE")
    assert load_settings().exit_mode == "atr"


def _cand(ctx_levels, mode, direction=1):
    s = Settings(exit_mode=mode)
    ctx = make_ctx(RAW, FEATS.index[400], s)
    ctx.levels = ctx_levels
    r = ctx.df.iloc[-1]
    c = {"strategy": "trend_pullback", "dir": direction, "stop_atr": 1.5, "t1_r": 1.5, "t2_r": 3.0, "atr": float(r.atr), "close": float(r.close), "plan_mode": "atr"}
    _apply_chart_plan(ctx, c)
    return ctx, c


def test_candidate_gets_chart_plan_as_equivalent_multiples_when_mode_is_chart():
    px = float(make_ctx(RAW, FEATS.index[400]).df.close.iloc[-1])
    atr = float(make_ctx(RAW, FEATS.index[400]).df.atr.iloc[-1])
    lv = {"ok": True, "zones": [Z(px - 2.2 * atr, px - 2.0 * atr, "Strong", "support"), Z(px + 5 * atr, px + 5.5 * atr, "Strong", "resistance")]}
    _, c = _cand(lv, "chart")
    assert c["plan_mode"] == "chart" and abs(c["stop_atr"] * atr - (px - c["stop_price"])) < 1e-6
    assert abs(px + c["t1_r"] * c["stop_atr"] * atr - c["t1_price"]) < 1e-6
    _, c2 = _cand(lv, "atr")
    assert c2["plan_mode"] == "atr" and c2["stop_atr"] == 1.5 and c2["chart_plan"]["ok"]


def test_reviewer_vetoes_a_candidate_without_a_clean_chart_plan():
    ctx, c = _cand({"ok": True, "zones": []}, "chart")
    assert c["plan_mode"] == "chart_none"
    ctx.candidate = c
    rep = AdversarialReviewer().run(ctx)      # runs with no other reports: the missing chart plan alone must produce a veto
    assert rep.recommendation == "VETO" and any("no clean plan" in v for v in rep.findings["vetoes"])


def test_ledger_uses_chart_prices_for_longs_and_shorts_and_cancels_gapped_entries():
    s = Settings()
    i = 300
    day_end = int(np.flatnonzero(FEATS.date.values == FEATS.date.iloc[i])[-1])
    bars = FEATS.iloc[: day_end + 40]
    now = FEATS.index[day_end + 39] + pd.Timedelta(minutes=5)
    atr = float(FEATS.atr.iloc[i])
    entry_px = float(FEATS.open.iloc[i + 1])
    base = {"label": "x@1#t", "strategy": "trend_pullback", "signal_time": str(FEATS.index[i]), "atr": atr, "stop_atr": 1.5, "t1_r": 1.5, "t2_r": 3.0,
            "max_hold_bars": 36, "shares": 100, "regime": "trend", "instrument": "SPY shares (simulated)", "evidence_status": "NO_BACKTEST", "plan_mode": "chart"}
    led = PaperLedger(s, tmpdir())
    led.open_position({**base, "dir": 1, "stop_price": entry_px - 3 * atr, "t1_price": entry_px + 6 * atr, "t2_price": entry_px + 9 * atr})
    led.update(bars, now)
    row = led.closed().iloc[0]
    assert abs(float(row["risk_per_share"]) - 3 * atr) < 0.2 * atr
    led2 = PaperLedger(s, tmpdir())
    led2.open_position({**base, "dir": -1, "stop_price": entry_px + 3 * atr, "t1_price": entry_px - 6 * atr, "t2_price": entry_px - 9 * atr})
    led2.update(bars, now)
    assert abs(float(led2.closed().iloc[0]["risk_per_share"]) - 3 * atr) < 0.2 * atr
    led3 = PaperLedger(s, tmpdir())
    led3.open_position({**base, "dir": 1, "stop_price": entry_px + 1.0, "t1_price": entry_px + 6 * atr, "t2_price": entry_px + 9 * atr})
    ev = led3.update(bars, now)
    assert any(e["type"] == "CANCELLED" for e in ev) and led3.positions() == [] and len(led3.closed()) == 0


def test_scan_rows_carry_chart_plan_fields_on_signals():
    s = Settings(exit_mode="chart")
    fake = {"ok": True, "zones": []}
    for r in scan(s, FEATS, levels=fake):
        if r["status"] == "SIGNAL":
            assert r["plan_mode"] == "chart_none" and "No practice trade" in signal_text(r)


def test_present_lines():
    from spxai import present as P
    ok = L.structure_plan(A, 100.0, 1.5, True)
    assert "These levels come from the chart" in P.chart_plan_line({"chart_plan": ok, "plan_mode": "chart"})
    assert "Chart-based alternative" in P.chart_plan_line({"chart_plan": ok, "plan_mode": "atr"})
    bad = L.structure_plan(None, 100.0, 1.5, True)
    assert "would pass" in P.chart_plan_line({"chart_plan": bad, "plan_mode": "atr"})
    assert P.chart_plan_line({}) == ""


def test_evaluate_end_to_end_in_chart_mode_vetoes_or_trades_with_chart_prices():
    from spxai import runner
    from tests.test_system import RAW as R2, SIG_T, _fetch_factory
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(R2, SIG_T)
    s = Settings(exit_mode="chart")
    runner.ingest(s, now, fetch, base=base)
    plan = runner.evaluate(s, now, fetch, force=True, base=base)
    assert plan["decision"] in ("CALL", "PUT", "NO TRADE")
    if plan["decision"] in ("CALL", "PUT"):
        assert plan["exit_plan"].startswith("chart-based") and plan["chart_plan_alternative"]
        pos = runner.PaperLedger(s, base).positions()
        assert pos and pos[0]["plan_mode"] == "chart" and pos[0]["stop_price"] and pos[0]["t1_price"]
    else:
        txt = str(plan.get("failed_requirements", ""))
        assert isinstance(txt, str)
    assert (base / "levels.json").exists()
