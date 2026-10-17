"""Streamlit dashboard (read-only). PAPER TRADING ONLY. Nothing here can place an order.

Reads the state written by the scheduled jobs: from a local folder (SPXAI_STATE_DIR) or from the
`data` branch of your GitHub repo (SPXAI_STATE_URL = https://raw.githubusercontent.com/<you>/<repo>/data).
Live chart data is fetched on demand (Alpaca free keys in Streamlit secrets, else Yahoo fallback).
"""
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "SPXAI_STATE_URL", "GITHUB_TOKEN"):  # Streamlit secrets -> env
    try:
        if k in st.secrets and not os.environ.get(k):
            os.environ[k] = str(st.secrets[k])
    except Exception:  # noqa: BLE001  no secrets file locally
        pass

from spxai import calendar as cal
from spxai.config import TZ, load_settings, state_dir
from spxai.features import add_features, make_hourly
from spxai.providers import NO_OPTIONS_MSG, REGISTRY, cost_violations, get_intraday

S = load_settings()
st.set_page_config(page_title="SPY Multi-Agent Research (Paper)", layout="wide")

# ------------------------------------------------------------------ state IO

URL = os.environ.get("SPXAI_STATE_URL", "").rstrip("/")


def _headers():
    tok = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"token {tok}"} if tok else {}


@st.cache_data(ttl=45, show_spinner=False)
def read_json(name):
    if URL:
        import requests
        r = requests.get(f"{URL}/{name}", headers=_headers(), timeout=15)
        return r.json() if r.ok else None
    p = state_dir() / name
    return json.loads(p.read_text()) if p.exists() else None


@st.cache_data(ttl=45, show_spinner=False)
def read_text(name):
    if URL:
        import requests
        r = requests.get(f"{URL}/{name}", headers=_headers(), timeout=15)
        return r.text if r.ok else ""
    p = state_dir() / name
    return p.read_text() if p.exists() else ""


@st.cache_data(ttl=45, show_spinner=False)
def read_jsonl(name, last=200):
    rows = []
    for line in read_text(name).splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
    return rows[-last:]


@st.cache_data(ttl=45, show_spinner=False)
def read_csv(name):
    t = read_text(name)
    return pd.read_csv(io.StringIO(t)) if t.strip() else pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def live_bars():
    """Returns (df, source, error). Falls back to stored history if the live fetch fails."""
    try:
        df, src, _ = get_intraday(S, days=10)
        return df, src, None
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    try:
        if URL:
            import requests
            r = requests.get(f"{URL}/bars/{S.symbol}_5m.csv.gz", headers=_headers(), timeout=30)
            df = pd.read_csv(io.BytesIO(r.content), index_col=0, compression="gzip")
        else:
            from spxai.store import load_bars
            df = load_bars(S.symbol, "5m")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert(TZ)
        return df, "stored history (live fetch failed)", err
    except Exception as e2:  # noqa: BLE001
        return pd.DataFrame(), "none", f"{err}; stored: {e2}"


def breaks(x_index):
    days = pd.date_range(x_index.min().normalize(), x_index.max().normalize())
    off = [d for d in days if not cal.is_trading_day(d.date())]
    return [dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour"), dict(values=[d for d in off if d.weekday() < 5])]


def candles(df, title, ema_cols, extra_lines=None, levels=None, rsi=True):
    x = df.index.tz_localize(None)
    fig = make_subplots(rows=2 if rsi else 1, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22] if rsi else [1], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=x, open=df.open, high=df.high, low=df.low, close=df.close, name="Price"), row=1, col=1)
    for col, name in ema_cols:
        if col in df:
            fig.add_trace(go.Scatter(x=x, y=df[col], name=name, mode="lines", line=dict(width=1.3)), row=1, col=1)
    for col, name in (extra_lines or []):
        if col in df:
            fig.add_trace(go.Scatter(x=x, y=df[col], name=name, mode="lines", line=dict(width=1, dash="dot")), row=1, col=1)
    for y, label in (levels or []):
        if y is not None:
            fig.add_hline(y=y, line_dash="dash", line_width=1, annotation_text=label, annotation_position="top left", row=1, col=1)
    if rsi and "rsi" in df:
        fig.add_trace(go.Scatter(x=x, y=df.rsi, name="RSI 14", line=dict(width=1.2)), row=2, col=1)
        for lv in (30, 70):
            fig.add_hline(y=lv, line_dash="dot", line_width=1, row=2, col=1)
    fig.update_xaxes(rangebreaks=breaks(x))
    fig.update_layout(height=620, title=title, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=1.06))
    return fig


# --------------------------------------------------------------------- page

st.title("SPY multi-agent research dashboard")
st.error("PAPER TRADING / RESEARCH ONLY. Nothing here is an order, a recommendation, or a guarantee. "
         + NO_OPTIONS_MSG)

run = read_json("latest_run.json") or {}
plan, agents = run.get("plan", {}), run.get("agents", {})
health = read_json("health.json") or {}
bt = read_json("backtest_latest.json") or {}

c1, c2, c3, c4 = st.columns(4)
dec = plan.get("decision", "no run yet")
c1.metric("Latest decision", dec)
c2.metric("Data status", plan.get("data_status", "n/a"))
c3.metric("Source", plan.get("data_source", "n/a"))
c4.metric("As of", str(plan.get("timestamp", "n/a"))[:19])
if plan.get("data_status") == "delayed":
    st.warning("Data is labelled DELAYED/unverified (unofficial source). Verify prices on your broker before acting.")

tabs = st.tabs(["Trade plan", "Charts", "Agents", "Backtests", "Paper performance", "Data sources & health", "Logs", "Trade log (2 years)"])

with tabs[0]:
    if dec in ("CALL", "PUT"):
        st.success(f"{dec}: {plan.get('decision_meaning')}")
        st.warning(plan.get("evidence_warning") or "Evidence status: supported on underlying data only.")
        st.write(f"**Execution basis:** {plan.get('execution_basis')}")
        a, b, c = st.columns(3)
        a.metric("Reference price", plan.get("underlying_reference_price"))
        b.metric("Underlying stop", plan["stop_loss_invalidation"]["underlying_stop"])
        c.metric("Max planned loss", f"${plan.get('max_planned_loss_usd')}")
        a.metric("Target 1", plan.get("target_1"))
        b.metric("Target 2", plan.get("target_2"))
        c.metric("Time exit", plan.get("time_exit"))
        st.json({k: plan.get(k) for k in ("strategy_label", "underlying_entry_trigger", "confirmation_requirements", "entry_rule",
                                          "proposed_max_entry_price", "position_size", "estimated_transaction_costs_usd",
                                          "reward_to_risk_t1", "reward_to_risk_blended", "supporting_evidence",
                                          "contradictory_evidence", "reviewer_warnings", "cancel_conditions", "decision_expires",
                                          "option_contract", "option_contract_note")})
    else:
        st.info("NO TRADE is a normal and frequent outcome.")
        for f in plan.get("failed_requirements", []):
            st.write(f"- **{f['requirement']}**: {f['detail']}")
        if plan.get("setups_forming"):
            st.write("Setups forming:")
            for f in plan["setups_forming"]:
                st.write(f"- {f['strategy']}: {f['text']}")
        if plan.get("re_evaluate_when"):
            st.caption("Next evaluation: " + plan["re_evaluate_when"])
    if plan.get("agent_disagreements"):
        st.subheader("Agent disagreements")
        for d in plan["agent_disagreements"]:
            st.write("- " + d)
    st.subheader("Option chain")
    st.write(agents.get("options_contract_analyst", {}).get("findings", {}).get("message", NO_OPTIONS_MSG))

with tabs[1]:
    df, src, err = live_bars()
    if err:
        st.warning(f"Live fetch problem: {err}")
    if len(df) < 120:
        st.info("Not enough bars to draw charts yet. Run the ingest job first.")
    else:
        days = st.slider("Days shown on the 5-minute chart", 1, 5, 2)
        f = add_features(df)
        last_days = sorted(set(f.date))[-days:]
        v = f[f.date.isin(last_days)]
        lv = [(plan.get("stop_loss_invalidation", {}).get("underlying_stop"), "stop"), (plan.get("target_1"), "T1"), (plan.get("target_2"), "T2")] \
            if dec in ("CALL", "PUT") else []
        lr = f.iloc[-1]
        lv += [(None if pd.isna(lr.pdh) else float(lr.pdh), "prior-day high"), (None if pd.isna(lr.pdl) else float(lr.pdl), "prior-day low")]
        st.plotly_chart(candles(v, f"{S.symbol} 5-minute ({src})", [("vwap", "VWAP"), ("ema9", "EMA 9"), ("ema21", "EMA 21")],
                                [("or_high", "Opening range high"), ("or_low", "Opening range low")], lv), use_container_width=True)
        h = make_hourly(df)
        hb = df.resample("60min", offset="30min", closed="left", label="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
        hb = hb.join(h[["h_ema20"]])
        hb = hb[hb.index.date >= sorted(set(hb.index.date))[-min(15, len(set(hb.index.date)))]]
        st.plotly_chart(candles(hb, f"{S.symbol} 1-hour (built from 5-minute bars)", [("h_ema20", "EMA 20")], rsi=False), use_container_width=True)
        r = agents.get("market_regime_analyst", {}).get("findings", {})
        if r:
            st.write(f"**Regime:** {r.get('classification')} | structure: {r.get('structure_1h')} | ADX {r.get('adx_5m')} | "
                     f"volatility: {r.get('volatility')} | suitable: {', '.join(r.get('suitable_strategies', [])) or 'none'}")
            st.json(r.get("levels", {}))
        vx = agents.get("volatility_event_analyst", {}).get("findings", {}).get("vix")
        st.write("**VIX (Yahoo, unofficial):** " + (json.dumps(vx) if vx else "unavailable"))

with tabs[2]:
    if not agents:
        st.info("No agent run recorded yet.")
    for name, a in agents.items():
        with st.expander(f"{name}  ->  {a['recommendation']}" + (f"   [ERROR: {a['error']}]" if a.get("error") else "")):
            st.write(f"Confidence interpretation: {a['confidence']}")
            if a["missing_data"]:
                st.write("Missing data: " + "; ".join(a["missing_data"]))
            if a["risk_concerns"]:
                st.write("Risk concerns: " + "; ".join(map(str, a["risk_concerns"])))
            if a["contradictory_evidence"]:
                st.write("Contradictory evidence: " + "; ".join(a["contradictory_evidence"]))
            st.json(a["findings"])

with tabs[3]:
    if "strategies" not in bt:
        st.info("No backtest report yet (it needs about 5 days of stored bars; the daily job creates it).")
    else:
        st.warning(bt["report_type"])
        st.error("Options profitability research: " + bt["options_profitability_research"]["status"] + ". "
                 + bt["options_profitability_research"]["reason"])
        for w in bt.get("warnings", []):
            st.warning(w)
        rows = []
        for n, r in bt["strategies"].items():
            o = r["out_of_sample_reported_once"]
            rows.append({"strategy": n, "OOS trades": o.get("trades", 0), "OOS net exp (R)": o.get("net_expectancy_R"),
                         "CI low": o.get("ci_low"), "CI high": o.get("ci_high"), "verdict": r["verdict"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption(f"Data: {bt['data']['first'][:10]} to {bt['data']['last'][:10]} ({bt['data']['trading_days']} days). "
                   f"Configurations tested: {bt['multiple_testing']['configurations_evaluated']}. "
                   f"Baseline OOS: {bt['baseline_hourly_trend']['out_of_sample'].get('net_expectancy_R')}R")
        pick = st.selectbox("Strategy detail", list(bt["strategies"]))
        st.json(bt["strategies"][pick])
        st.write("Limitations: " + " ".join(bt["limitations"]))

with tabs[4]:
    pt = read_csv("paper_trades.csv")
    st.caption("SIMULATED SPY share trades, not option fills. Win rate alone says little; look at expectancy and sample size.")
    open_pos = read_json("open_positions.json") or []
    st.write(f"Open simulated positions: {len(open_pos)}")
    if len(pt) == 0:
        st.info("No closed paper trades yet.")
    else:
        pt["cum_R"] = pt.net_R.cumsum()
        a, b, c, d = st.columns(4)
        a.metric("Closed trades", len(pt))
        b.metric("Win rate", f"{(pt.net_R > 0).mean():.0%}")
        c.metric("Expectancy (R)", f"{pt.net_R.mean():.2f}")
        d.metric("Total P&L (sim $)", f"{pt.pnl_usd.sum():,.0f}")
        if len(pt) < 30:
            st.warning("Fewer than 30 closed trades: too few to conclude anything.")
        st.line_chart(pt.set_index(pd.RangeIndex(len(pt)))["cum_R"])
        st.dataframe(pt.tail(100), use_container_width=True)
    perf = agents.get("performance_adaptation_analyst", {}).get("findings", {})
    if perf:
        st.json(perf)

with tabs[5]:
    st.subheader("Verified data-source capabilities and cost")
    st.dataframe(pd.DataFrame([c.__dict__ for c in REGISTRY]), use_container_width=True)
    v = cost_violations(S)
    st.success("$0 data budget: no violations") if not v else st.error("Budget violations: " + "; ".join(v))
    st.subheader("Ingest health")
    st.json(health)
    dq = agents.get("data_quality_monitor", {}).get("findings", {})
    if dq:
        st.subheader("Latest data-quality check")
        st.json(dq)

with tabs[6]:
    st.subheader("Alerts (simulated, not orders)")
    st.dataframe(pd.DataFrame(read_jsonl("alerts.jsonl", 100)).iloc[::-1] if read_jsonl("alerts.jsonl", 1) else pd.DataFrame(),
                 use_container_width=True)
    st.subheader("Decision log (today)")
    today = str(pd.Timestamp.now(tz=TZ).date())
    rows = read_jsonl(f"runs/{today}.jsonl", 300)
    st.dataframe(pd.DataFrame(rows).iloc[::-1] if rows else pd.DataFrame(), use_container_width=True)

if st.sidebar.checkbox("Auto-refresh every 60 s", value=False):
    time.sleep(60)
    st.rerun()
st.sidebar.caption("Control: create a file named KILL_SWITCH in the state branch (or set SPXAI_KILL=1) to pause all signals.")


with tabs[7]:
    ts = read_json("trade_summary_2y.json")
    tl = read_csv("trades_2y.csv")
    if not ts or "strategies" not in ts or not ts["strategies"]:
        st.info("No trade log yet. Run the 'Daily ingest and backtest' job on GitHub (it needs at least ~5 days of stored bars).")
    else:
        w = ts.get("window", {})
        st.warning(f"Replay of the fixed default rules on {w.get('first', '?')} to {w.get('last', '?')}. "
                   "SPY shares only. A WIN means the net result after costs was above zero. "
                   "This is NOT an out-of-sample test and not a prediction; the Backtests tab holds the fairer test.")
        st.caption(f"Dollar figures assume a fixed ${ts['risk_per_trade_usd_illustrative']:,.0f} risk per trade (1R), no compounding. "
                   "Illustration only.")
        rows = []
        for n, r in ts["strategies"].items():
            rows.append({"strategy": n, "trades": r["trades"], "wins": r["wins"], "losses": r["losses"],
                         "win rate": r["win_rate"], "avg win (R)": r["avg_win_R"], "avg loss (R)": r["avg_loss_R"],
                         "net total (R)": r["total_net_R"], "avg per trade (R)": r["net_exp_R"],
                         "illustrative $": r["total_pnl_usd_illustrative"], "worst drop (R)": r["max_drawdown_R"]})
        st.subheader("Wins and losses by strategy")
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        pick2 = st.selectbox("See the conditions for", list(ts["strategies"]), key="tl_pick")
        sel = ts["strategies"][pick2]
        labels = {"direction": "By direction (long = buy, short = sell)", "regime": "By market regime",
                  "tod_bucket": "By time of day", "trigger": "By entry trigger", "month": "By month"}
        for col, title in labels.items():
            data = sel["by"].get(col, {})
            if data:
                st.markdown("**" + title + "**")
                st.dataframe(pd.DataFrame(data).T[["trades", "wins", "losses", "win_rate", "total_net_R", "net_exp_R"]],
                             use_container_width=True)
        if len(tl):
            st.subheader("Every trade")
            mine = tl[tl.strategy == pick2]
            res = st.multiselect("Show", ["WIN", "LOSS", "FLAT"], default=["WIN", "LOSS", "FLAT"], key="tl_res")
            mine = mine[mine.result.isin(res)]
            st.dataframe(mine.drop(columns=["strategy"]), use_container_width=True, height=420)
            st.download_button("Download all trades (CSV)", tl.to_csv(index=False), "trades_2y.csv", "text/csv")

