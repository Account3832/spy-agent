"""SPY Trading Assistant: read-only dashboard. PRACTICE (PAPER) MODE ONLY. Nothing here can place an order.

Reads the state written by the scheduled jobs: from a local folder (SPXAI_STATE_DIR) or from the
`data` branch of your GitHub repo (SPXAI_STATE_URL = https://raw.githubusercontent.com/<you>/<repo>/data).
All wording and number formatting lives in spxai/present.py (unit-tested); this file only lays things out.
"""
import html
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
from spxai import present as P
from spxai.config import TZ, load_settings, state_dir
from spxai.features import add_features, make_hourly
from spxai.providers import NO_OPTIONS_MSG, REGISTRY, cost_violations, get_intraday
from spxai.strategies import REGISTRY as STRATS

S = load_settings()
st.set_page_config(page_title="SPY Trading Assistant", page_icon="📈", layout="wide")

GREEN, RED, AMBER, BLUE = "#16a34a", "#dc2626", "#d97706", "#2563eb"

st.markdown("""
<style>
.block-container{padding-top:2.2rem;max-width:1180px}
h1,h2,h3{letter-spacing:-.01em}
.app-title{font-size:2rem;font-weight:750;margin:0;letter-spacing:-.02em}
.app-sub{opacity:.65;margin:.2rem 0 .9rem 0}
.chip{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.76rem;font-weight:650;margin:0 6px 6px 0;
  border:1px solid rgba(128,128,128,.35);background:rgba(128,128,128,.10)}
.chip.good{color:#16a34a;border-color:rgba(22,163,74,.45);background:rgba(22,163,74,.12)}
.chip.bad{color:#dc2626;border-color:rgba(220,38,38,.45);background:rgba(220,38,38,.12)}
.chip.warn{color:#d97706;border-color:rgba(217,119,6,.5);background:rgba(217,119,6,.13)}
.chip.blue{color:#2563eb;border-color:rgba(37,99,235,.45);background:rgba(37,99,235,.12)}
.hero{border-radius:20px;padding:26px 30px;margin:.4rem 0 1rem 0;border:1px solid rgba(128,128,128,.28);
  background:linear-gradient(135deg,rgba(128,128,128,.10),rgba(128,128,128,.03))}
.hero.good{background:linear-gradient(135deg,rgba(22,163,74,.20),rgba(128,128,128,.03));border-color:rgba(22,163,74,.5)}
.hero.bad{background:linear-gradient(135deg,rgba(220,38,38,.20),rgba(128,128,128,.03));border-color:rgba(220,38,38,.5)}
.hero .eyebrow{font-size:.74rem;letter-spacing:.09em;text-transform:uppercase;opacity:.6;font-weight:650}
.hero .headline{font-size:2.3rem;font-weight:780;line-height:1.15;margin:.25rem 0 .35rem 0;letter-spacing:-.02em}
.hero .sub{font-size:1.02rem;opacity:.8;max-width:760px}
.kpi{border-radius:14px;padding:14px 18px;border:1px solid rgba(128,128,128,.25);background:rgba(128,128,128,.06);height:100%}
.kpi .label{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;opacity:.65;font-weight:650}
.kpi .value{font-size:1.65rem;font-weight:740;margin-top:2px}
.kpi .value.good{color:#16a34a}.kpi .value.bad{color:#dc2626}
.kpi .note{font-size:.8rem;opacity:.65;margin-top:2px}
.card{border-radius:16px;padding:18px 20px;border:1px solid rgba(128,128,128,.28);background:rgba(128,128,128,.05);height:100%;margin-bottom:12px}
.card .t{font-weight:720;font-size:1.05rem}
.card .b{font-size:.8rem;opacity:.62;min-height:2.6rem;margin:2px 0 8px 0}
.card .big{font-size:2rem;font-weight:780;letter-spacing:-.02em;line-height:1.1}
.card .big.good{color:#16a34a}.card .big.bad{color:#dc2626}
.card .n{font-size:.82rem;opacity:.72;margin-top:3px}
.bar{height:7px;border-radius:99px;background:rgba(128,128,128,.22);margin:10px 0 6px 0;overflow:hidden}
.bar>div{height:100%;border-radius:99px;background:#2563eb}
.sec{font-size:1.15rem;font-weight:720;margin:1.2rem 0 .2rem 0}
.hint{font-size:.88rem;opacity:.7;margin-bottom:.6rem}
button[data-baseweb="tab"]{font-weight:650}
</style>
""", unsafe_allow_html=True)


def esc(x):
    return html.escape(str(x))


def chip(text, tone=""):
    return f'<span class="chip {esc(tone)}">{esc(text)}</span>'


def kpi(label, value, note="", tone=""):
    return (f'<div class="kpi"><div class="label">{esc(label)}</div><div class="value {esc(tone)}">{esc(value)}</div>'
            f'<div class="note">{esc(note)}</div></div>')


def kpi_row(items):
    for col, it in zip(st.columns(len(items)), items):
        col.markdown(kpi(*it), unsafe_allow_html=True)


def section(title, hint=""):
    st.markdown(f'<div class="sec">{esc(title)}</div>' + (f'<div class="hint">{esc(hint)}</div>' if hint else ""),
                unsafe_allow_html=True)


def money_tone(x):
    return "good" if x and x > 0 else ("bad" if x and x < 0 else "")


def price(x):
    return "n/a" if x is None else f"${float(x):,.2f}"


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
        return df, "saved history (live fetch failed)", err
    except Exception as e2:  # noqa: BLE001
        return pd.DataFrame(), "none", f"{err}; stored: {e2}"


def breaks(x_index):
    days = pd.date_range(x_index.min().normalize(), x_index.max().normalize())
    off = [d for d in days if not cal.is_trading_day(d.date())]
    return [dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour"), dict(values=[d for d in off if d.weekday() < 5])]


def style(fig, height=None):
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(size=13),
                      margin=dict(l=10, r=10, t=50, b=10), legend=dict(orientation="h", y=1.08))
    if height:
        fig.update_layout(height=height)
    return fig


def candles(df, title, ema_cols, extra_lines=None, levels=None, rsi=True):
    x = df.index.tz_localize(None)
    fig = make_subplots(rows=2 if rsi else 1, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22] if rsi else [1], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=x, open=df.open, high=df.high, low=df.low, close=df.close, name="Price",
                                 increasing_line_color=GREEN, decreasing_line_color=RED), row=1, col=1)
    for col, name in ema_cols:
        if col in df:
            fig.add_trace(go.Scatter(x=x, y=df[col], name=name, mode="lines", line=dict(width=1.5)), row=1, col=1)
    for col, name in (extra_lines or []):
        if col in df:
            fig.add_trace(go.Scatter(x=x, y=df[col], name=name, mode="lines", line=dict(width=1, dash="dot")), row=1, col=1)
    for y, label in (levels or []):
        if y is not None:
            fig.add_hline(y=y, line_dash="dash", line_width=1, annotation_text=label, annotation_position="top left", row=1, col=1)
    if rsi and "rsi" in df:
        fig.add_trace(go.Scatter(x=x, y=df.rsi, name="Momentum (RSI)", line=dict(width=1.2)), row=2, col=1)
        for lv in (30, 70):
            fig.add_hline(y=lv, line_dash="dot", line_width=1, row=2, col=1)
    fig.update_xaxes(rangebreaks=breaks(x))
    fig.update_layout(title=title, xaxis_rangeslider_visible=False)
    return style(fig, 600)


@st.cache_data(ttl=3600, show_spinner=False)
def stored_bars():
    """All saved 5-minute bars (about 5 years), used to draw past trades. Empty frame if unavailable."""
    try:
        if URL:
            import requests
            r = requests.get(f"{URL}/bars/{S.symbol}_5m.csv.gz", headers=_headers(), timeout=60)
            r.raise_for_status()
            try:
                df = pd.read_csv(io.BytesIO(r.content), index_col=0, compression="gzip")
            except Exception:  # noqa: BLE001  already decompressed in transit
                df = pd.read_csv(io.BytesIO(r.content), index_col=0)
        else:
            from spxai.store import load_bars
            df = load_bars(S.symbol, "5m")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert(TZ)
        return df
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def trade_chart(tr, heading):
    """Candles for the trade's day with entry, stop, targets and exit drawn on. Returns None if the day's prices are unavailable."""
    bars = stored_bars()
    live, _, _ = live_bars()
    if len(live):
        bars = pd.concat([bars, live]) if len(bars) else live
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    if bars is None or len(bars) == 0:
        return None
    day = pd.Timestamp(tr["date"]).date()
    lo = pd.Timestamp(day, tz=TZ) - pd.Timedelta(days=14)
    hi = pd.Timestamp(day, tz=TZ) + pd.Timedelta(days=1)
    seg = bars[(bars.index >= lo) & (bars.index < hi)]
    if not (seg.index.date == day).any():
        return None
    f = add_features(seg)
    v = f[f.date == day]
    fig = candles(v, heading, [("vwap", "Day's average price (VWAP)"), ("ema9", "Fast average"), ("ema21", "Slow average")],
                  [("or_high", "Morning range top"), ("or_low", "Morning range bottom")], None, rsi=False)
    k = 1 if tr["direction"] == "LONG" else -1
    entry, stop, t1, t2, ex = (float(tr[x]) for x in ("entry", "stop_price", "target1_price", "target2_price", "exit_price"))
    et = pd.Timestamp(tr["entry_time"]).tz_convert(TZ).tz_localize(None)
    xt = pd.Timestamp(tr["exit_time"]).tz_convert(TZ).tz_localize(None) + pd.Timedelta(minutes=2.5)
    x1 = max(xt, et + pd.Timedelta(minutes=5))
    fig.add_shape(type="rect", x0=et, x1=x1, y0=entry, y1=t2, fillcolor="rgba(22,163,74,0.13)", line_width=0, layer="below")
    fig.add_shape(type="rect", x0=et, x1=x1, y0=entry, y1=stop, fillcolor="rgba(220,38,38,0.13)", line_width=0, layer="below")
    for y, lab, col, dash in ((entry, "Entry", BLUE, "solid"), (stop, "Stop (exit if wrong)", RED, "dash"),
                              (t1, "Target 1", GREEN, "dash"), (t2, "Target 2", GREEN, "dot")):
        fig.add_shape(type="line", x0=et, x1=x1, y0=y, y1=y, line=dict(color=col, width=1.6, dash=dash))
        fig.add_annotation(x=x1, y=y, text=f"{lab} {y:,.2f}", showarrow=False, xanchor="left", font=dict(size=11, color=col))
    win = tr["result"] == "WIN"
    fig.add_trace(go.Scatter(x=[et], y=[entry], mode="markers+text", name="Got in", text=["Got in"],
                             textposition="top center" if k == 1 else "bottom center",
                             marker=dict(symbol="triangle-up" if k == 1 else "triangle-down", size=16, color=BLUE,
                                         line=dict(width=1, color="white"))))
    fig.add_trace(go.Scatter(x=[xt], y=[ex], mode="markers+text", name="Got out",
                             text=["Got out: " + ("win" if win else ("loss" if tr["result"] == "LOSS" else "even"))],
                             textposition="bottom center" if k == 1 else "top center",
                             marker=dict(symbol="x", size=14, color=GREEN if win else RED, line=dict(width=2, color=GREEN if win else RED))))
    ys = [float(v.low.min()), float(v.high.max()), stop, t2]
    pad = (max(ys) - min(ys)) * 0.06
    fig.update_yaxes(range=[min(ys) - pad, max(ys) + pad])
    return fig


# --------------------------------------------------------------------- data

run = read_json("latest_run.json") or {}
plan, agents = run.get("plan", {}), run.get("agents", {})
health = read_json("health.json") or {}
bt = read_json("backtest_latest.json") or {}
ts = read_json("trade_summary_2y.json") or {}
tl = read_csv("trades_2y.csv")
RISK = ts.get("risk_per_trade_usd_illustrative") or S.account_equity * S.max_risk_pct / 100.0
NOW = pd.Timestamp.now(tz=TZ)
dec = plan.get("decision", "no run yet")

# ------------------------------------------------------------------- header

st.markdown('<div class="app-title">SPY Trading Assistant</div>'
            '<div class="app-sub">Practice mode: fake money. It shows what the rules would do. It never places an order.</div>',
            unsafe_allow_html=True)
fresh_label, fresh_tone = P.freshness(plan, NOW)
mkt = ("Market open", "good") if cal.is_open(NOW) else ("Market closed", "")
st.markdown(chip("PRACTICE MODE", "blue") + chip(mkt[0], mkt[1]) + chip(fresh_label, fresh_tone)
            + chip("Source: " + str(plan.get("data_source", "n/a")).replace("_", " ")), unsafe_allow_html=True)
st.caption(NO_OPTIONS_MSG + " Trades are simulated with SPY shares.")
if plan.get("data_status") == "delayed":
    st.warning("The price data is delayed or from an unofficial source. Check prices with your broker before acting.")

tabs = st.tabs(["Today", "Charts", "2-year results", "Reliability test", "Paper trades", "Agents", "Data & health", "Logs"])

# -------------------------------------------------------------------- TODAY

with tabs[0]:
    v = P.decision_view(plan)
    st.markdown(f'<div class="hero {esc(v["tone"] if v["tone"] in ("good", "bad") else "")}">'
                f'<div class="eyebrow">What the rules say right now</div><div class="headline">{esc(v["headline"])}</div>'
                f'<div class="sub">{esc(v["sub"])}</div></div>', unsafe_allow_html=True)
    if dec in ("CALL", "PUT"):
        if plan.get("evidence_warning"):
            st.warning(P.plain_text(str(plan["evidence_warning"])))
        stop = (plan.get("stop_loss_invalidation") or {}).get("underlying_stop")
        kpi_row([("Price when it triggered", price(plan.get("underlying_reference_price")), "SPY", ""),
                 ("Exit if it goes wrong", price(stop), "stop price", "bad"),
                 ("First target", price(plan.get("target_1")), "take half the profit", "good"),
                 ("Second target", price(plan.get("target_2")), "take the rest", "good")])
        st.write("")
        kpi_row([("Most you could lose", P.usd(float(plan["max_planned_loss_usd"]), sign=False) if plan.get("max_planned_loss_usd") is not None else "n/a",
                  "if the stop is hit", ""),
                 ("Get out by", str(plan.get("time_exit") or "n/a"), "latest exit time", ""),
                 ("Size", str((plan.get("position_size") or {}).get("shares", "n/a")) if isinstance(plan.get("position_size"), dict) else "n/a",
                  "SPY shares (simulated)", ""),
                 ("Expires", str(plan.get("decision_expires") or "n/a")[:19], "after this the idea is stale", "")])
        for title, key in (("Reasons for", "supporting_evidence"), ("Reasons against", "contradictory_evidence"),
                           ("Warnings", "reviewer_warnings"), ("Cancel the idea if", "cancel_conditions")):
            items = plan.get(key) or []
            if items:
                section(title)
                for it in (items if isinstance(items, list) else [items]):
                    st.write("- " + P.plain_text(str(it)))
        with st.expander("Technical details"):
            st.json({k: plan.get(k) for k in ("strategy_label", "underlying_entry_trigger", "entry_rule", "reward_to_risk_t1",
                                              "reward_to_risk_blended", "estimated_transaction_costs_usd", "execution_basis",
                                              "option_contract", "option_contract_note")})
    else:
        left, right = st.columns(2)
        with left:
            section("Why not?", "Everything below must be true before the rules will suggest a trade.")
            fr = plan.get("failed_requirements") or []
            if fr:
                for f in fr:
                    st.write("✗ " + P.plain_requirement(f))
            else:
                st.write("No reason recorded yet.")
        with right:
            section("Getting close", "Ideas that are forming but have not triggered. They are not trade signals.")
            sf = plan.get("setups_forming") or []
            if sf:
                for f in sf:
                    st.markdown(f"**{esc(P.strategy_name(f.get('strategy', '')))}**  \n{esc(P.plain_text(str(f.get('text', ''))))}")
            else:
                st.write("Nothing is close to triggering.")
    st.caption("Next check: " + P.next_check_text(NOW) + ".")
    if plan.get("agent_disagreements"):
        section("Where the checkers disagreed")
        for d in plan["agent_disagreements"]:
            st.write("- " + P.plain_text(str(d)))
    pt0 = read_csv("paper_trades.csv")
    open0 = read_json("open_positions.json") or []
    section("Practice account at a glance")
    kpi_row([("Open practice trades", str(len(open0)), "right now", ""),
             ("Closed practice trades", str(len(pt0)), "so far (aim for 100+)", ""),
             ("Practice result", P.usd(pt0.pnl_usd.sum()) if len(pt0) and "pnl_usd" in pt0 else "$0", "fake money",
              money_tone(pt0.pnl_usd.sum()) if len(pt0) and "pnl_usd" in pt0 else "")])

# ------------------------------------------------------------------- CHARTS

with tabs[1]:
    df, src, err = live_bars()
    if err:
        st.warning("Could not get the newest prices, so this shows saved history. " + str(err)[:160])
    if len(df) < 120:
        st.info("Not enough price history to draw charts yet. Run the 'Daily ingest and backtest' job on GitHub first.")
    else:
        section("5-minute chart", "Each candle is 5 minutes. Green = price rose, red = price fell.")
        days = st.slider("Days shown", 1, 5, 2)
        f = add_features(df)
        last_days = sorted(set(f.date))[-days:]
        vw = f[f.date.isin(last_days)]
        lv = [(plan.get("stop_loss_invalidation", {}).get("underlying_stop"), "exit if wrong"), (plan.get("target_1"), "target 1"),
              (plan.get("target_2"), "target 2")] if dec in ("CALL", "PUT") else []
        lr = f.iloc[-1]
        lv += [(None if pd.isna(lr.pdh) else float(lr.pdh), "yesterday's high"), (None if pd.isna(lr.pdl) else float(lr.pdl), "yesterday's low")]
        st.plotly_chart(candles(vw, f"SPY, 5-minute candles ({src})", [("vwap", "Day's average price (VWAP)"), ("ema9", "Fast average"), ("ema21", "Slow average")],
                                [("or_high", "Morning range top"), ("or_low", "Morning range bottom")], lv), use_container_width=True)
        h = make_hourly(df)
        hb = df.resample("60min", offset="30min", closed="left", label="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
        hb = hb.join(h[["h_ema20"]])
        hb = hb[hb.index.date >= sorted(set(hb.index.date))[-min(15, len(set(hb.index.date)))]]
        section("1-hour chart", "The bigger picture. The rules only take trades that agree with this trend.")
        st.plotly_chart(candles(hb, "SPY, 1-hour candles (built from the 5-minute data)", [("h_ema20", "Hourly trend line")], rsi=False),
                        use_container_width=True)
        r = agents.get("market_regime_analyst", {}).get("findings", {})
        if r:
            suit = ", ".join(P.strategy_name(x) for x in r.get("suitable_strategies", [])) or "none"
            st.write(f"**Market type:** {P.REGIMES.get(r.get('classification'), r.get('classification'))}  ·  "
                     f"**Best-suited rule sets right now:** {suit}")
        with st.expander("What are these lines?"):
            st.markdown("- **Day's average price (VWAP):** the average price paid today, weighted by volume.\n"
                        "- **Fast / slow average:** smoothed price lines. When price pulls back to the slow line in an uptrend, that can be a buying chance.\n"
                        "- **Morning range:** the high and low of the first 30 minutes of trading.\n"
                        "- **Momentum (RSI):** below 30 means price fell fast, above 70 means it rose fast.")

# ------------------------------------------------------------- 2-YEAR RESULTS

with tabs[2]:
    if not ts or not ts.get("strategies"):
        st.info("No results yet. Run the 'Daily ingest and backtest' job on GitHub (it needs at least about 5 days of saved data).")
    else:
        w = ts.get("window", {})
        st.markdown(f"**What would have happened** if you had followed each set of rules from **{esc(w.get('first', '?'))}** to "
                    f"**{esc(w.get('last', '?'))}**, risking **{esc(P.usd(RISK, sign=False))}** on every trade "
                    f"(0.5% of a $25,000 practice account)?")
        st.info("This replays fixed rules over the whole 2 years. It is not a prediction, and it can look better than live trading would. "
                "The 'Reliability test' tab has the fairer check. SPY shares only.")
        sb = P.scoreboard(ts, bt, RISK, live=S.enabled_strategies)
        cols = st.columns(3)
        for i, row in sb.iterrows():
            key = row["_key"]
            tone = "good" if row["Total result ($)"] > 0 else ("bad" if row["Total result ($)"] < 0 else "")
            cols[i % 3].markdown(
                f'<div class="card"><div class="t">{esc(row["Strategy"])}</div><div class="b">{esc(P.strategy_blurb(key))}</div>'
                f'<div class="big {tone}">{esc(P.usd(row["Total result ($)"]))}</div><div class="n">total result over 2 years</div>'
                f'<div class="bar"><div style="width:{max(0, min(100, row["Win rate"])):.0f}%"></div></div>'
                f'<div class="n"><b>{int(row["Wins"])}</b> wins · <b>{int(row["Losses"])}</b> losses · {row["Win rate"]:.0f}% win rate</div>'
                f'<div class="n">Average win {esc(P.usd(row["Average win ($)"]))} · average loss {esc(P.usd(row["Average loss ($)"]))}</div>'
                f'<div class="n">Worst losing stretch {esc(P.usd(row["Worst losing stretch ($)"]))}</div>'
                f'<div style="margin-top:8px">{chip("Fair test: " + row["Fair test"], row["_tone"])}'
                f'{chip(row["Status"], "blue" if row["Status"] == "Live practice" else "")}</div></div>',
                unsafe_allow_html=True)
        lesson = P.win_rate_lesson(sb)
        if lesson:
            st.info(lesson)
        with st.expander("See the full scoreboard as a table"):
            show = sb.drop(columns=["_key", "_tone"])
            st.dataframe(show, use_container_width=True, hide_index=True, column_config={
                "Win rate": st.column_config.ProgressColumn("Win rate", min_value=0, max_value=100, format="%.0f%%"),
                "Average win ($)": st.column_config.NumberColumn(format="$%.0f"),
                "Average loss ($)": st.column_config.NumberColumn(format="$%.0f"),
                "Average per trade ($)": st.column_config.NumberColumn(format="$%.2f"),
                "Total result ($)": st.column_config.NumberColumn(format="$%.0f"),
                "Worst losing stretch ($)": st.column_config.NumberColumn(format="$%.0f")})
        ec = P.equity_curves(tl)
        if len(ec):
            section("Running total over time", "Each line is the profit or loss so far if you had followed that rule set. Rising is good.")
            fig = go.Figure()
            for c in ec.columns:
                fig.add_trace(go.Scatter(x=ec.index, y=ec[c], name=c, mode="lines", line=dict(width=2)))
            fig.add_hline(y=0, line_width=1, line_dash="dot")
            fig.update_layout(yaxis_title="Profit or loss so far ($)")
            st.plotly_chart(style(fig, 420), use_container_width=True)

        section("Look closer at one rule set", "When and how did it win or lose?")
        keys = list(sb["_key"])
        pick = st.selectbox("Rule set", keys, format_func=P.strategy_name, key="tl_pick")
        sel = ts["strategies"][pick]
        st.caption(P.strategy_blurb(pick))
        cfg = {"Win rate": st.column_config.ProgressColumn("Win rate", min_value=0, max_value=100, format="%.0f%%"),
               "Total ($)": st.column_config.NumberColumn(format="$%.0f"),
               "Average per trade ($)": st.column_config.NumberColumn(format="$%.2f")}
        c1, c2 = st.columns(2)
        for box, kind, title, noun in ((c1, "direction", "Buying vs selling", "direction"), (c2, "regime", "Type of market", "market type"),
                                       (c1, "tod_bucket", "Time of day", "time slot")):
            data = sel["by"].get(kind, {})
            if data:
                ct = P.condition_table(data, kind, RISK)
                with box:
                    st.markdown(f"**{title}**")
                    st.dataframe(ct.drop(columns=["_k"]), use_container_width=True, hide_index=True, column_config=cfg)
                    st.caption(P.best_worst(ct, noun))
        mo = sel["by"].get("month", {})
        if mo:
            mt = P.condition_table(mo, "month", RISK)
            with c2:
                st.markdown("**Month by month**")
                fig = go.Figure(go.Bar(x=mt["Group"], y=mt["Total ($)"], marker_color=[GREEN if x >= 0 else RED for x in mt["Total ($)"]]))
                fig.update_layout(yaxis_title="Result ($)", showlegend=False)
                st.plotly_chart(style(fig, 280), use_container_width=True)
                st.caption("Green months made money, red months lost money.")
        if len(tl):
            section("Every trade", "One row per trade. A win means it made money after costs.")
            show_what = st.radio("Show", ["All trades", "Wins only", "Losses only"], horizontal=True, key="tl_res")
            mine = tl[tl.strategy == pick]
            if show_what == "Wins only":
                mine = mine[mine.result == "WIN"]
            elif show_what == "Losses only":
                mine = mine[mine.result == "LOSS"]
            tv = P.trades_view(mine)
            st.dataframe(tv, use_container_width=True, hide_index=True, height=420,
                         column_config={"Result ($)": st.column_config.NumberColumn(format="$%.0f")})
            st.download_button("Download every trade (spreadsheet)", tl.to_csv(index=False), "trades_2y.csv", "text/csv")
            if len(mine) and "stop_price" in mine:
                section("See a trade on the chart", "Pick any trade to see where it got in, where the stop and targets were, and where it got out.")
                recent = mine.sort_values("entry_time", ascending=False).head(300).reset_index(drop=True)
                ti = st.selectbox("Trade", list(range(len(recent))), format_func=lambda j: P.trade_option_label(recent.iloc[j]), key="tl_trade")
                tr = recent.iloc[ti].to_dict()
                head = (f"{P.strategy_name(pick)}: {'Buy' if tr['direction'] == 'LONG' else 'Sell'} · "
                        f"{tr['result']} {P.usd(tr['pnl_usd_illustrative'])} · {P.exit_text(tr['exit_reason'])}")
                fig = trade_chart(tr, head)
                if fig is None:
                    st.info("The price history for that day is not available here yet.")
                else:
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption("Blue triangle = where it got in. Green zone = room to profit up to Target 2. Red zone = room to lose down to the stop. "
                               "X = the average price where it got out (both halves, after costs).")
            elif len(mine):
                st.info("Trade prices for the chart appear after the next run of the 'Daily ingest and backtest' job.")

# ---------------------------------------------------------- RELIABILITY TEST

with tabs[3]:
    if "strategies" not in bt:
        st.info("No reliability test yet. The daily job on GitHub creates it once about 5 days of data are saved.")
    else:
        d = bt.get("data", {})
        oos = (d.get("splits_days") or {}).get("oos")
        st.markdown("**Is any of this reliable?** The system tests each rule set on the **most recent slice of the data** "
                    "(about " + esc(oos if oos else "a quarter of the") + " trading days) that it never used when choosing the settings. "
                    "That is closer to how the rules would behave on brand-new days.")
        rt = P.reliability_table(bt, RISK)
        st.dataframe(rt, use_container_width=True, hide_index=True, column_config={
            "Average per trade ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Plausible range, low ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Plausible range, high ($)": st.column_config.NumberColumn(format="$%.2f")})
        st.caption("Plausible range: where the true average per trade probably lies. If it includes $0, the result could be luck.")
        base = (bt.get("baseline_hourly_trend") or {}).get("out_of_sample", {})
        if base.get("trades"):
            st.write(f"**Yardstick:** the simple 1-hour trend rule averaged {P.usd(P.r_to_usd(base.get('net_expectancy_R'), RISK), decimals=2)} "
                     f"per trade over {base['trades']} trades. A rule set should beat this before it is worth the extra complexity.")
        section("What the verdicts mean")
        for code in ("UNDERLYING_EDGE_SUPPORTED", "NOT_DEMONSTRATED", "INSUFFICIENT_SAMPLE", "FAILED_OOS"):
            lab, tone, expl = P.verdict_info(code)
            st.markdown(chip(lab, tone) + " " + esc(expl), unsafe_allow_html=True)
        opt = bt.get("options_profitability_research", {})
        st.info("**Option profits: " + str(opt.get("status", "UNAVAILABLE")).lower() + ".** " + str(opt.get("reason", "")))
        for wmsg in bt.get("warnings", []):
            st.warning(wmsg)
        st.caption(f"Data used: {str(d.get('first', ''))[:10]} to {str(d.get('last', ''))[:10]} ({d.get('trading_days', '?')} trading days). "
                   f"{bt.get('multiple_testing', {}).get('configurations_evaluated', '?')} settings were tried, and the statistics are adjusted for that.")
        with st.expander("Limitations to keep in mind"):
            for lim in bt.get("limitations", []):
                st.write("- " + lim)
        with st.expander("Technical details for one rule set"):
            p2 = st.selectbox("Rule set", list(bt["strategies"]), format_func=P.strategy_name, key="bt_pick")
            st.json(bt["strategies"][p2])

# ------------------------------------------------------------- PAPER TRADES

with tabs[4]:
    pt = read_csv("paper_trades.csv")
    st.caption("Practice trades with fake money, using SPY shares (not options). These are the best check of the rules, because they happen on new days.")
    open_pos = read_json("open_positions.json") or []
    if open_pos:
        section("Open right now")
        st.dataframe(pd.DataFrame(open_pos), use_container_width=True, hide_index=True)
    if len(pt) == 0:
        st.info("No practice trades have finished yet. The system only trades when every requirement is met, so this can take days or weeks. "
                "Aim for at least 100 before drawing any conclusion.")
    else:
        wins = int((pt.net_R > 0).sum())
        pnl = pt.pnl_usd if "pnl_usd" in pt else pt.net_R * RISK
        kpi_row([("Finished trades", str(len(pt)), "aim for 100+", ""),
                 ("Wins", f"{wins} of {len(pt)}", f"{wins / len(pt):.0%} win rate", ""),
                 ("Average per trade", P.usd(pnl.mean(), decimals=2), "after costs", money_tone(pnl.mean())),
                 ("Total result", P.usd(pnl.sum()), "fake money", money_tone(pnl.sum()))])
        if len(pt) < 30:
            st.warning("Fewer than 30 finished trades: far too few to conclude anything.")
        fig = go.Figure(go.Scatter(x=list(range(1, len(pt) + 1)), y=pnl.cumsum(), mode="lines", line=dict(width=2.2)))
        fig.update_layout(xaxis_title="Trade number", yaxis_title="Running total ($)")
        st.plotly_chart(style(fig, 320), use_container_width=True)
        st.dataframe(pt.tail(100), use_container_width=True, hide_index=True)
        need = {"dir", "strategy", "entry", "risk_per_share", "net_R", "entry_time", "exit_time", "exit_reason"}
        if need <= set(pt.columns):
            section("See a practice trade on the chart")
            rec = pt.tail(100).iloc[::-1].reset_index(drop=True)
            pi = st.selectbox("Trade", list(range(len(rec))), key="pt_trade",
                              format_func=lambda j: f"{str(rec.loc[j, 'entry_time'])[:16]}  ·  {P.strategy_name(str(rec.loc[j, 'strategy']))}")
            ptr = P.paper_trade_dict(rec.iloc[pi], STRATS, RISK)
            pfig = trade_chart(ptr, f"{P.strategy_name(ptr['strategy'])}: {ptr['result']} {P.usd(ptr['pnl_usd_illustrative'])}")
            if pfig is None:
                st.info("The price history for that day is not available here yet.")
            else:
                st.plotly_chart(pfig, use_container_width=True)
    perf = agents.get("performance_adaptation_analyst", {}).get("findings", {})
    if perf:
        with st.expander("Technical details"):
            st.json(perf)

# ------------------------------------------------------------------- AGENTS

with tabs[5]:
    st.caption("Eleven small checkers each look at one part of the picture. A separate risk checker can say no. None of them places an order.")
    if not agents:
        st.info("No checker results recorded yet.")
    for name, a in agents.items():
        rec = str(a.get("recommendation", ""))
        tone = ("good" if rec in ("OK", "APPROVE", "NO_OBJECTION", "CONTRACT_FOUND") else
                ("bad" if rec in ("REJECT", "VETO", "NO_DATA", "DISABLE_SIGNALS", "BLACKOUT", "NO_CONTRACT") else
                 ("warn" if rec == "CAUTION" else "")))
        with st.expander(f"{name.replace('_', ' ').title()}  -  {rec}" + (f"   (problem: {a['error']})" if a.get("error") else "")):
            st.markdown(chip(rec, tone) + chip("confidence: " + str(a.get("confidence", "n/a"))), unsafe_allow_html=True)
            for lab, key in (("Missing data", "missing_data"), ("Risk concerns", "risk_concerns"), ("Evidence against", "contradictory_evidence")):
                if a.get(key):
                    st.write(f"**{lab}:** " + "; ".join(P.plain_text(str(x)) for x in a[key]))
            st.caption("Technical details")
            st.json(a.get("findings", {}))

# ------------------------------------------------------------ DATA & HEALTH

with tabs[6]:
    viol = cost_violations(S)
    kpi_row([("Data source", str(health.get("source", plan.get("data_source", "n/a"))).replace("_", " "), "where prices come from", ""),
             ("Days saved", str(health.get("history_days", "n/a")), "trading days of 5-minute data", ""),
             ("Data check", str(health.get("quality", "n/a")), "OK means no gaps found", "good" if health.get("quality") == "OK" else ""),
             ("Data cost", "$0" if not viol else "Over budget", "free sources only", "good" if not viol else "bad")])
    if health.get("warning"):
        st.warning(health["warning"])
    if viol:
        st.error("Budget problem: " + "; ".join(viol))
    section("Where the data can come from", "Free sources only. Real-time option quotes cost money, so they are switched off.")
    st.dataframe(pd.DataFrame([c.__dict__ for c in REGISTRY]), use_container_width=True, hide_index=True)
    with st.expander("Technical details: last download and data check"):
        st.json(health)
        dq = agents.get("data_quality_monitor", {}).get("findings", {})
        if dq:
            st.json(dq)

# --------------------------------------------------------------------- LOGS

with tabs[7]:
    section("Alerts", "Messages the system generated. They are notes, not orders.")
    al = read_jsonl("alerts.jsonl", 100)
    st.dataframe(pd.DataFrame(al).iloc[::-1] if al else pd.DataFrame(), use_container_width=True, hide_index=True)
    section("Every check made today")
    today = str(pd.Timestamp.now(tz=TZ).date())
    rows = read_jsonl(f"runs/{today}.jsonl", 300)
    st.dataframe(pd.DataFrame(rows).iloc[::-1] if rows else pd.DataFrame(), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ SIDEBAR

st.sidebar.markdown("### How to read the numbers")
for term, text in P.GLOSSARY:
    st.sidebar.markdown(f"**{term}**  \n{text.format(risk=P.usd(RISK, sign=False))}")
st.sidebar.markdown("---")
st.sidebar.caption("Pause everything: create a file named KILL_SWITCH on the data branch (or set SPXAI_KILL=1).")
st.sidebar.caption("Practice mode only. Not financial advice. Past results do not predict future results.")
if st.sidebar.checkbox("Refresh this page every 60 seconds", value=False):
    time.sleep(60)
    st.rerun()
