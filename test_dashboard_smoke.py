"""Runs dashboard/app.py end to end against real engine output, using stand-ins for streamlit and plotly.

streamlit is not installable in the build sandbox, so this cannot prove the page LOOKS right. It does prove the
file executes without NameError/KeyError/TypeError on a no-trade state, on a trade state, and on an empty state,
and that the key wording and the exact options-unavailable message are emitted.
"""
import os
import runpy
import sys
from pathlib import Path
from unittest import mock

import pandas as pd

from spxai import runner
from spxai.config import Settings
from spxai.providers import NO_OPTIONS_MSG
from tests.helpers import synth, tmpdir
from tests.test_system import RAW, SIG_T, _fetch_factory

APP = Path(__file__).resolve().parent.parent / "dashboard" / "app.py"
NY = "America/New_York"


def _fake_modules(texts):
    st = mock.MagicMock(name="streamlit")
    st.cache_data.side_effect = lambda *a, **k: (lambda f: f)
    st.columns.side_effect = lambda spec, **k: [mock.MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
    st.tabs.side_effect = lambda labels: [mock.MagicMock() for _ in labels]
    st.selectbox.side_effect = lambda label, options, *a, **k: (k.get("format_func", str)(list(options)[0]), list(options)[0])[1]
    st.radio.side_effect = lambda label, options, *a, **k: list(options)[0]
    st.slider.side_effect = lambda label, lo, hi, val=None, *a, **k: val if val is not None else lo
    st.checkbox.return_value = False
    st.sidebar.checkbox.return_value = False
    for name in ("markdown", "caption", "info", "warning", "error", "success", "write", "subheader"):
        getattr(st, name).side_effect = lambda *a, **k: texts.append(str(a[0]) if a else "")
    st.sidebar.markdown.side_effect = lambda *a, **k: texts.append(str(a[0]) if a else "")
    st.sidebar.caption.side_effect = lambda *a, **k: texts.append(str(a[0]) if a else "")
    st.expander.return_value.__enter__.return_value = mock.MagicMock()
    plotly, go, sub = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
    plotly.graph_objects, plotly.subplots = go, sub      # `import plotly.graph_objects as go` resolves via the package attribute
    return {"streamlit": st, "plotly": plotly, "plotly.graph_objects": go, "plotly.subplots": sub}


def _run_app(base, url="", mods_out=None):
    texts = []
    env = {"SPXAI_STATE_DIR": str(base), "SPXAI_STATE_URL": url}
    mods = _fake_modules(texts)
    if mods_out is not None:
        mods_out.update(mods)
    with mock.patch.dict(sys.modules, mods), mock.patch.dict(os.environ, env):
        os.environ.pop("SPXAI_STATE_URL") if not url else None
        runpy.run_path(str(APP), run_name="dashboard_app")
    return "\n".join(texts)


def test_dashboard_runs_on_empty_state():
    out = _run_app(tmpdir())
    assert "SPY TERMINAL" in out and NO_OPTIONS_MSG in out
    assert "Waiting for the first check" in out


def test_dashboard_runs_with_no_trade_and_full_backtest():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(RAW, SIG_T)
    runner.ingest(Settings(), now, fetch, base=base)
    runner.backtest_job(Settings(), base)
    closed = pd.Timestamp("2026-10-12 11:00", tz=NY)
    runner.evaluate(Settings(), closed, fetch, force=True, base=base)   # force: stored bars, may or may not trigger
    out = _run_app(base)
    assert "What would have happened" in out and "Is any of this reliable?" in out
    assert NO_OPTIONS_MSG in out and "Next check:" in out
    assert "R)" not in out.split("What would have happened")[1][:400]    # no raw R jargon in the intro


def test_dashboard_runs_with_a_trade_plan_and_paper_position():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(RAW, SIG_T)
    runner.ingest(Settings(), now, fetch, base=base)
    runner.backtest_job(Settings(), base)
    plan = runner.evaluate(Settings(), now, fetch, force=True, base=base)
    assert plan["decision"] in ("CALL", "PUT")
    out = _run_app(base)
    assert ("Bullish setup" in out) or ("Bearish setup" in out)
    assert "Practice account at a glance" in out


def test_dynamic_text_is_html_escaped():
    base = tmpdir()
    runner.write_json(base / "latest_run.json", {"plan": {"decision": "NO TRADE", "data_source": "<script>alert(1)</script>",
                                                          "timestamp": "2026-10-16 15:50:22-04:00", "data_status": "live"},
                                                 "agents": {}}) if hasattr(runner, "write_json") else None
    from spxai.store import write_json
    write_json(base / "latest_run.json", {"plan": {"decision": "NO TRADE", "data_source": "<script>alert(1)</script>",
                                                   "timestamp": "2026-10-16 15:50:22-04:00", "data_status": "live"}, "agents": {}})
    out = _run_app(base)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_dashboard_draws_entry_stop_targets_and_exit_on_the_trade_chart():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(RAW, SIG_T)
    runner.ingest(Settings(), now, fetch, base=base)
    runner.backtest_job(Settings(), base)
    mods = {}
    out = _run_app(base, mods_out=mods)
    assert "See a trade on the chart" in out
    fig = mods["plotly.subplots"].make_subplots.return_value
    assert fig.add_shape.call_count >= 6          # 2 coloured zones + 4 level lines for the chosen trade
    labels = " ".join(str(c.kwargs.get("text", "")) for c in fig.add_annotation.call_args_list)
    for word in ("Entry", "Stop", "Target 1", "Target 2"):
        assert word in labels
    markers = [c.kwargs.get("name") for c in mods["plotly.graph_objects"].Scatter.call_args_list]
    assert "Got in" in markers and "Got out" in markers


def test_dashboard_lists_research_only_candidates_and_the_win_rate_lesson():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    runner.ingest(Settings(), now, _fetch_factory(RAW, SIG_T), base=base)
    runner.backtest_job(Settings(), base)
    out = _run_app(base)
    assert "A high win rate is not the same as making money" in out


def test_dashboard_shows_spread_ideas_and_terminal_header():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(RAW, SIG_T)
    runner.ingest(Settings(), now, fetch, base=base)
    runner.backtest_job(Settings(), base)
    out = _run_app(base)
    assert "Credit spread ideas on SPY" in out and "ESTIMATE" in out
    assert "Bear call spread" in out and "Bull put spread" in out
    assert "Win rate needed to break even" in out and "Most you can lose" in out
    assert "SPY TERMINAL" in out and "NO ORDERS EVER SENT" in out and 'class="tape"' in out
    assert "<script>" not in out


def test_spread_tab_degrades_without_history():
    out = _run_app(tmpdir())
    assert "Credit spread ideas on SPY" in out and "Not enough saved prices yet" in out


def test_monitor_tab_lists_every_strategy_and_phone_alert_help():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(RAW, SIG_T)
    runner.ingest(Settings(), now, fetch, base=base)
    runner.backtest_job(Settings(), base)
    runner.evaluate(Settings(), now, fetch, force=True, base=base)
    assert (base / "monitor.json").exists()
    import json
    rows = json.loads((base / "monitor.json").read_text())["rows"]
    assert len(rows) == 13
    out = _run_app(base)
    assert "Live monitor" in out and "Phone alerts (Telegram)" in out
    assert "Used saved check" in out or "Checked just now" in out
    assert "@BotFather" in out and "Never paste the token" in out


def test_monitor_tab_degrades_without_data():
    out = _run_app(tmpdir())
    assert "Live monitor" in out and "Nothing to monitor yet" in out


def test_levels_tab_with_data_and_without():
    base = tmpdir()
    now = SIG_T + pd.Timedelta(minutes=5, seconds=30)
    fetch = _fetch_factory(RAW, SIG_T)
    runner.ingest(Settings(), now, fetch, base=base)
    runner.backtest_job(Settings(), base)
    runner.evaluate(Settings(), now, fetch, force=True, base=base)
    assert (base / "levels.json").exists()
    out = _run_app(base)
    assert "Support and resistance zones and the trend on four time scales" in out
    empty = _run_app(tmpdir())
    assert "Support and resistance zones and the trend on four time scales" in empty and "Not enough price history yet" in empty
