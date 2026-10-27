"""The trader-style zone checklist (research only) in the SPY project."""
import pandas as pd

from spxai import levels as L
from spxai.backtest import run_research, simulate
from spxai.config import Settings
from spxai.features import add_features
from spxai.monitor import scan
from spxai.strategies import REGISTRY
from tests.helpers import synth


def _chart(kind="PULLBACK_IN_UPTREND", zones=None):
    z = zones if zones is not None else [{"low": 98.0, "high": 99.0, "mid": 98.5, "strength": "Strong", "touches": 4, "role": "support"}]
    return {"ok": True, "alignment": {"kind": kind}, "zones": z}


def test_confluence_check_long_and_short():
    ok = L.confluence_check(_chart(), entry=100.0, atr=1.0, bar_low=98.9, bar_close=99.8)
    assert ok["ok"] and ok["zone"]["mid"] == 98.5
    assert not L.confluence_check(_chart("ALIGNED_DOWN"), 100.0, 1.0, 98.9, 99.8)["ok"]
    assert not L.confluence_check(_chart(), 103.0, 1.0, 98.9, 99.8)["ok"]
    assert not L.confluence_check(None, 100.0, 1.0, 98.9, 99.8)["ok"]
    res = [{"low": 101.0, "high": 102.0, "mid": 101.5, "strength": "Strong", "touches": 4, "role": "resistance"}]
    sh = L.confluence_check(_chart("BOUNCE_IN_DOWNTREND", res), 100.0, 1.0, 99.5, 100.4, bar_high=101.2, long=False)
    assert sh["ok"] and sh["zone"]["mid"] == 101.5
    assert not L.confluence_check(_chart("ALIGNED_UP", res), 100.0, 1.0, 99.5, 100.4, bar_high=101.2, long=False)["ok"]


def test_zone_rejection_is_research_only_and_needs_the_engine():
    s = Settings()
    assert "zone_rejection" in s.research_only_strategies and "zone_rejection" not in s.enabled_strategies
    raw = synth(days=40, seed=3)
    feats = add_features(raw)
    strat = REGISTRY["zone_rejection"]()
    assert len(simulate(feats, strat, s)) == 0                      # no engine -> not testable, never silently uses ATR exits
    eng = L.Engine(raw, L.STOCK_TFS, offset_min=30, session_end="16:00")
    skips = []
    tr = simulate(feats, strat, s, engine=eng, skips=skips)
    assert len(tr) + len(skips) > 0
    for r in tr.itertuples():
        assert r.risk > 0 and r.dir in (1, -1)


def test_research_report_includes_it_only_with_the_engine():
    raw = synth(days=40, seed=3)
    feats = add_features(raw)
    s = Settings()
    assert "zone_rejection" not in run_research(feats, s, grid=False)["strategies"]
    rep = run_research(feats, s, grid=False, engine=L.Engine(raw, L.STOCK_TFS, offset_min=30, session_end="16:00"))
    assert "zone_rejection" in rep["strategies"]
    assert rep["strategies"]["zone_rejection"]["verdict"] in ("INSUFFICIENT_SAMPLE", "NOT_DEMONSTRATED", "FAILED_OOS", "EDGE_SUPPORTED", "UNDERLYING_EDGE_SUPPORTED")


def test_scan_labels_it_research_only():
    raw = synth(days=40, seed=3)
    feats = add_features(raw)
    rows = scan(Settings(), feats, levels=L.analyze(raw, L.STOCK_TFS, offset_min=30, session_end="16:00"))
    z = [r for r in rows if r["strategy"] == "zone_rejection"]
    assert len(z) == 1 and z[0]["kind"] == "Research only"
