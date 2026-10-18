import json

import numpy as np
import pandas as pd

from spxai import levels as L
from spxai.features import add_features
from spxai.strategies import REGISTRY
from tests.helpers import synth


def zigzag(n_swings=12, leg=12, start=100.0, step=2.0, drift=1.0, tf="15min"):
    """Deterministic zig-zag: each leg moves up `step`+drift or down `step`, so highs and lows both rise (drift>0) or fall (drift<0)."""
    pts, p = [start], start
    for i in range(n_swings):
        p += (step + drift) if i % 2 == 0 else -step
        pts.append(p)
    close = np.concatenate([np.linspace(pts[i], pts[i + 1], leg, endpoint=False) for i in range(len(pts) - 1)])
    idx = pd.date_range("2026-01-01", periods=len(close), freq=tf, tz="UTC")
    return pd.DataFrame({"open": close, "high": close + 0.05, "low": close - 0.05, "close": close, "volume": 1.0}, index=idx)


def test_pivots_are_confirmed_after_the_fact():
    d = zigzag()
    p = L.pivots(d, 3, 3)
    assert len(p) >= 6
    assert ((p.cpos - p.pos) == 3).all() and p.cpos.max() < len(d)
    assert set(p.kind) == {"H", "L"}


def test_structure_up_and_down():
    up = L.structure(L.pivots(zigzag(drift=1.0), 3, 3), 110.0, 1.0)
    dn = L.structure(L.pivots(zigzag(drift=-1.0), 3, 3), 90.0, 1.0)
    assert up["direction"] == "UP" and up["state"] == "intact" and up["invalidation"] == up["swing_low"]
    assert dn["direction"] == "DOWN"


def test_structure_marks_break():
    p = L.pivots(zigzag(drift=1.0), 3, 3)
    s = L.structure(p, close=50.0, atr_now=1.0)          # far below the last higher low
    assert s["direction"] == "UP" and s["state"] == "broken"


def test_add_structure_values_on_zigzag():
    d = L.add_structure(zigzag(drift=1.0).copy(), 3, 3)
    assert d.sw_trend.iloc[-1] == 1 and d.sw_hi.iloc[-1] > d.sw_hi_prev.iloc[-1] and d.sw_lo.iloc[-1] > d.sw_lo_prev.iloc[-1]


def test_alignment_cases():
    def row(label, w, direction, state="intact"):
        return {"label": label, "weight": w, "direction": direction, "state": state}
    a = L.alignment([row("Primary", 3, "UP"), row("Secondary", 2, "UP"), row("Intermediate", 1.5, "DOWN"), row("Minor", 1, "DOWN")])
    assert a["kind"] == "PULLBACK_IN_UPTREND"
    b = L.alignment([row(x, 1, "UP") for x in ("Primary", "Secondary", "Intermediate", "Minor")])
    assert b["kind"] == "ALIGNED_UP" and b["score"] == 1.0
    c = L.alignment([row("Primary", 3, "DOWN"), row("Secondary", 2, "DOWN"), row("Intermediate", 1.5, "UP"), row("Minor", 1, "UP")])
    assert c["kind"] == "BOUNCE_IN_DOWNTREND"
    broken = L.alignment([row("Primary", 3, "UP", "broken"), row("Secondary", 2, "UP", "broken")])
    assert broken["bias"] == "NEUTRAL"


def test_zones_cluster_and_roles():
    now = pd.Timestamp("2026-01-10", tz="UTC")
    pts = [{"price": p, "kind": k, "w": 1.0, "time": now - pd.Timedelta(days=2), "tf": "1h", "label": "Intermediate"}
           for p, k in ((100.0, "L"), (100.2, "L"), (100.1, "H"), (110.0, "H"), (110.1, "H"))]
    z = L.build_zones(pts, price=105.0, tol=0.5, atr_ref=1.0, now=now)
    roles = sorted((round(x["mid"]), x["role"], x["touches"]) for x in z)
    assert roles == [(100, "support", 3), (110, "resistance", 2)]


def test_role_reversal_needs_two_touches():
    now = pd.Timestamp("2026-01-10", tz="UTC")
    mk = lambda p, k: {"price": p, "kind": k, "w": 1.0, "time": now, "tf": "1h", "label": "x"}
    z = L.build_zones([mk(100.0, "H"), mk(100.1, "H")], price=105.0, tol=0.5, atr_ref=1.0, now=now)
    assert z[0]["role"] == "support" and "role reversal" in (z[0]["former"] or "")
    z1 = L.build_zones([mk(100.0, "H")], price=105.0, tol=0.5, atr_ref=1.0, now=now)
    assert z1[0]["former"] is None


def test_room_to_levels_blocks_target():
    a = {"ok": True, "zones": [{"low": 103.0, "high": 103.5, "mid": 103.2, "strength": "Strong", "touches": 4, "role": "resistance"}]}
    r = L.room_to_levels(a, entry=100.0, risk=2.0, t1=104.0, t2=108.0, long=True)
    assert r["blocks_t1"] is True and r["dist_r"] == 1.5 and "BEFORE target 1" in r["text"]
    weak = {"ok": True, "zones": [{**a["zones"][0], "strength": "Weak"}]}
    assert L.room_to_levels(weak, 100.0, 2.0, 104.0, 108.0)["blocks_t1"] is False
    assert L.room_to_levels(None, 100.0, 2.0, 103.0, 106.0)["known"] is False



def test_add_structure_is_causal():
    d = synth(30)
    cols = ["sw_hi", "sw_hi_prev", "sw_lo", "sw_lo_prev", "sw_trend"]
    full = L.add_structure(d.copy(), 4, 4)
    part = L.add_structure(d.iloc[: 78 * 20].copy(), 4, 4)
    common = part.index[-300:]
    assert np.allclose(full.loc[common, cols].fillna(-1).values, part.loc[common, cols].fillna(-1).values)


def test_stock_hours_are_anchored_at_0930_and_forming_candle_dropped():
    d = synth(6)
    last_day = d.index[-1].date()
    cut = d[(d.index.date < last_day) | (d.index.strftime("%H:%M") <= "10:40")]      # last day ends 10:45 -> 10:30 candle unfinished
    h = L.resample_tf(cut, 60, offset_min=30, session_end="16:00")
    assert h.index[-1].strftime("%H:%M") == "09:30"
    assert set(h.index.strftime("%M")) == {"30"}
    dd = L.resample_tf(cut, "D", session_end="16:00")
    assert dd.index[-1].date() < last_day                        # today's daily candle is not finished


def test_analyze_stock_end_to_end_and_json():
    a = L.analyze(synth(80, seed=5), L.STOCK_TFS, offset_min=30, session_end="16:00")
    assert a["ok"] and len(a["timeframes"]) == 4 and a["zones"]
    json.dumps(a)
    assert [t["tf"] for t in a["timeframes"]] == ["1D", "1h", "15m", "5m"]


def test_new_structure_strategies_exist_and_signal_both_sides_safely():
    d = add_features(synth(60, seed=3))
    for n in ("support_bounce", "swing_breakout"):
        sig = REGISTRY[n]().signals(d)
        assert set(sig["dir"].unique()) <= {-1, 0, 1}
        assert n in __import__("spxai.config", fromlist=["Settings"]).Settings().research_only_strategies


def test_zone_levels_put_strikes_beyond_zones():
    from spxai import spreads as SP
    a = {"ok": True, "zones": [{"role": "resistance", "strength": "Strong", "touches": 5, "low": 610.0, "high": 611.0},
                               {"role": "support", "strength": "Moderate", "touches": 3, "low": 590.0, "high": 591.0},
                               {"role": "resistance", "strength": "Weak", "touches": 1, "low": 620.0, "high": 621.0},
                               {"role": "inside", "strength": "Strong", "touches": 4, "low": 599.0, "high": 601.0}]}
    z = SP.zone_levels(a)
    assert sorted(z.values()) == [590.0, 611.0] and SP.zone_levels(None) == {}


def test_signal_text_includes_chart_check_when_known():
    from spxai.monitor import signal_text
    r = {"direction": "LONG", "strategy": "support_bounce", "kind": "Research only", "entry_ref": 600.0, "stop": 598.0, "target1": 603.0,
         "target2": 606.0, "shares": 20, "risk_usd": 40.0, "plan_mode": "atr", "room": {"known": True, "text": "Next resistance zone: 602 (strong), 1.0R from entry - BEFORE target 1."}}
    assert "Chart check: Next resistance zone" in signal_text(r)
    r["room"] = {"known": False, "text": ""}
    assert "Chart check" not in signal_text(r)
