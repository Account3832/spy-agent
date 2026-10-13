"""Twelve specialised analyst agents with a shared structured-output protocol.

These agents are deterministic analysts (no paid language model is needed or called). They
exchange AgentReport objects. Each runs under a deadline with an error policy: failure or
timeout yields a report with recommendation NO_DATA, which the orchestrator treats as missing
evidence (so the system fails closed to NO TRADE).

SECURITY: no external free text (news, web pages, provider notes) is ever interpreted as an
instruction. Only numeric market data and the local config files influence decisions.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from . import calendar as cal
from .backtest import metrics
from .config import Settings
from .options import evaluate_contracts
from .providers import Capability, cost_violations
from .quality import QualityReport
from .risk import RiskManager
from .strategies import REGISTRY

AGENT_TIMEOUT_S = 15.0
CONTINUATION = {"trend_pullback", "orb_retest", "vol_expansion"}
REVERSAL = {"failed_breakout", "range_reversion"}


@dataclass
class Ctx:
    s: Settings
    now: pd.Timestamp
    df: pd.DataFrame                       # feature frame, COMPLETED bars only
    source: str
    cap: Capability
    data_status: str
    quality: QualityReport
    vix: pd.DataFrame | None = None
    chain: pd.DataFrame | None = None
    ledger: dict = field(default_factory=dict)
    backtest: dict | None = None
    paper: pd.DataFrame | None = None
    events: list | None = None
    kill: tuple = (False, "")
    breaker_state: str = "closed"
    health: dict = field(default_factory=dict)
    candidate: dict | None = None
    reports: dict = field(default_factory=dict)


@dataclass
class AgentReport:
    agent: str
    agent_version: str
    timestamp: str
    input_snapshot: dict
    findings: dict
    evidence: list
    confidence: str                         # qualitative only; never a made-up percentage
    contradictory_evidence: list
    recommendation: str
    invalidation_conditions: list
    missing_data: list
    risk_concerns: list
    strategy_version: str | None = None
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _snapshot(ctx: Ctx) -> dict:
    d = ctx.df
    return {"last_bar_start": str(d.index[-1]) if len(d) else None, "bars": int(len(d)), "source": ctx.source,
            "data_status": ctx.data_status, "close": float(d.close.iloc[-1]) if len(d) else None}


class Agent:
    name, version = "agent", "1.0"

    def run(self, ctx: Ctx) -> AgentReport:
        raise NotImplementedError

    def rep(self, ctx: Ctx, findings, recommendation, evidence=None, confidence="n/a", contradictory=None,
            invalidation=None, missing=None, risks=None, strategy_version=None) -> AgentReport:
        return AgentReport(self.name, self.version, str(ctx.now), _snapshot(ctx), findings, evidence or [], confidence,
                           contradictory or [], recommendation, invalidation or [], missing or [], risks or [],
                           strategy_version)


def run_agent(agent: Agent, ctx: Ctx, timeout: float = AGENT_TIMEOUT_S) -> AgentReport:
    t0 = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        rep = ex.submit(agent.run, ctx).result(timeout=timeout)
    except FutTimeout:
        rep = agent.rep(ctx, {}, "NO_DATA", missing=["agent timed out"], risks=[f"{agent.name} exceeded {timeout}s"])
        rep.error = "timeout"
    except Exception as e:  # noqa: BLE001
        rep = agent.rep(ctx, {}, "NO_DATA", missing=["agent crashed"], risks=[f"{agent.name}: {type(e).__name__}: {e}"])
        rep.error = f"{type(e).__name__}: {e}"
    finally:
        ex.shutdown(wait=False)
    rep.duration_ms = int((time.monotonic() - t0) * 1000)
    return rep


# --------------------------------------------------------------- 12: data

class DataQualityMonitor(Agent):
    name = "data_quality_monitor"

    def run(self, ctx):
        q, bad = ctx.quality, []
        if not q.ok:
            bad += [f"{i['code']}: {i['msg']}" for i in q.blocking]
        if ctx.breaker_state == "open":
            bad.append("provider circuit breaker is open")
        viol = cost_violations(ctx.s)
        if viol:
            bad.append("$0 data budget violated: " + "; ".join(viol))
        findings = {"data_status": ctx.data_status, "source": ctx.source, "provider_realtime_class": ctx.cap.realtime,
                    "quality": q.summary(), "stats": q.stats, "breaker": ctx.breaker_state,
                    "cost_violations": viol, "kill_switch": ctx.kill[0],
                    "options_data": "UNAVAILABLE (no free executable quotes)" if ctx.chain is None else "supplied"}
        if ctx.kill[0]:
            bad.append(f"kill switch: {ctx.kill[1]}")
        return self.rep(ctx, findings, "DISABLE_SIGNALS" if bad else "OK",
                        evidence=[{"claim": "bar completeness and freshness checked against the NYSE calendar", "source": "quality.check_bars"}],
                        risks=bad, missing=[] if ctx.chain is not None else ["executable SPY option quotes"])


# ----------------------------------------------------------- 1: regime

class RegimeAnalyst(Agent):
    name = "market_regime_analyst"

    def run(self, ctx):
        d, r = ctx.df, ctx.df.iloc[-1]
        trending = r.adx >= 22 and r.h_dir != 0
        label = ("bullish" if r.h_dir == 1 and r.adx >= 18 else "bearish" if r.h_dir == -1 and r.adx >= 18 else
                 "neutral" if r.h_dir == 0 and r.adx < 18 else "transitional")
        struct = ("higher highs and higher lows" if r.h_struct >= 2 else
                  "lower highs and lower lows" if r.h_struct <= -2 else "mixed structure")
        lv = {"prior_day_high": r.pdh, "prior_day_low": r.pdl, "prior_day_close": r.pdc, "opening_range_high": r.or_high,
              "opening_range_low": r.or_low, "vwap": r.vwap, "hourly_ema20": r.h_ema20,
              "swing_high_78": d.high.tail(78).max(), "swing_low_78": d.low.tail(78).min()}
        lv = {k: round(float(v), 2) for k, v in lv.items() if pd.notna(v)}
        above = min([v for v in lv.values() if v > r.close], default=None)
        below = max([v for v in lv.values() if v < r.close], default=None)
        suit = (["trend_pullback", "orb_retest", "vol_expansion"] if r.regime == "trend" else
                ["range_reversion", "failed_breakout"] if r.regime == "range" else [])
        agree = sum([r.h_dir == 1 and r.ema9 > r.ema21, r.h_dir == -1 and r.ema9 < r.ema21, r.adx >= 22, abs(r.h_struct) >= 2])
        conf = "moderate" if agree >= 3 else "low"
        side = "LONG" if label == "bullish" else "SHORT" if label == "bearish" else "NEUTRAL"
        inv = ([f"1h close below its 20-EMA ({r.h_ema20:.2f})"] if label == "bullish" else
               [f"1h close above its 20-EMA ({r.h_ema20:.2f})"] if label == "bearish" else ["a decisive 1h trend develops"])
        return self.rep(ctx, {"classification": label, "bar_regime": r.regime, "structure_1h": struct,
                              "adx_5m": round(float(r.adx), 1), "adx_1h": None if pd.isna(r.h_adx) else round(float(r.h_adx), 1),
                              "volatility": r.vol_regime, "levels": lv, "nearest_resistance": above, "nearest_support": below,
                              "suitable_strategies": suit, "minutes_to_close": round(cal.minutes_to_close(ctx.now))},
                        side, evidence=[{"claim": f"1h trend direction {int(r.h_dir)}, structure: {struct}", "source": "features.make_hourly (completed hours only)"}],
                        confidence=conf, contradictory=[] if agree >= 3 else ["trend indicators do not all agree"],
                        invalidation=inv)


# -------------------------------------------------------- 2: 5m entries

class EntryAnalyst(Agent):
    name = "five_minute_entry_analyst"

    def run(self, ctx):
        d, s = ctx.df, ctx.s
        assert d.index[-1] + pd.Timedelta(minutes=5) <= ctx.now + pd.Timedelta(seconds=1), "incomplete bar in frame"
        r = d.iloc[-1]
        cands, forming = [], []
        for name in s.enabled_strategies:
            st = REGISTRY[name]()
            sg = st.signals(d, s.entry_start, s.entry_end).iloc[-1]
            if sg["dir"] != 0:
                sp = st.spec
                cands.append({"strategy": name, "version": sp.version, "label": st.label(), "dir": int(sg["dir"]),
                              "trigger": sg["trigger"], "confirmation": sp.confirmation, "invalidation": sp.invalidation,
                              "stop_atr": st.stop_atr, "t1_r": st.t1_r, "t2_r": st.t2_r, "max_hold_bars": st.max_hold_bars,
                              "atr": float(r.atr), "close": float(r.close), "signal_time": str(d.index[-1]),
                              "regime": r.regime, "h_dir": int(r.h_dir), "filters": list(st.filters)})
            f = st.forming(d)
            if f and sg["dir"] == 0:
                forming.append({"strategy": name, "text": f})
        dirs = {c["dir"] for c in cands}
        if not cands:
            rec = "NO_TRADE"
        elif len(dirs) > 1:
            rec = "CONFLICT"
        else:
            rec = "LONG" if cands[0]["dir"] == 1 else "SHORT"
        return self.rep(ctx, {"candidates": cands, "setups_forming": forming, "last_bar_close_time": str(d.index[-1] + pd.Timedelta(minutes=5)),
                              "price_vs_vwap": None if pd.isna(r.vwap) else round(float(r.close - r.vwap), 3),
                              "atr_5m": round(float(r.atr), 3), "rsi": round(float(r.rsi), 1)},
                        rec, evidence=[{"claim": c["trigger"], "source": f"strategy {c['label']}"} for c in cands],
                        contradictory=["strategies disagree on direction"] if rec == "CONFLICT" else [],
                        invalidation=[c["invalidation"] for c in cands])


# --------------------------------------------------- 3: indicators/ablation

class IndicatorResearcher(Agent):
    name = "technical_indicator_researcher"

    def run(self, ctx):
        r = ctx.df.iloc[-1]
        vals = {k: (None if pd.isna(r[k]) else round(float(r[k]), 3)) for k in
                ("ema9", "ema21", "rsi", "atr", "adx", "vwap", "macd_hist", "bb_up", "bb_lo", "rvol", "rv")}
        bt = ctx.backtest or {}
        table, validated = {}, []
        for n, sr in (bt.get("strategies") or {}).items():
            table[n] = sr.get("ablation_train_val", {})
            if sr.get("verdict") == "UNDERLYING_EDGE_SUPPORTED":
                validated.append(n)
        note = ("No indicator or filter has demonstrated repeatable out-of-sample value yet; treat all as candidates."
                if not validated else f"Strategies with out-of-sample support (underlying only): {validated}")
        miss = [] if bt else ["backtest report (ablation results)"]
        return self.rep(ctx, {"latest_values": vals, "ablation_train_val_only": table, "note": note}, "INFO",
                        evidence=[{"claim": "ablation compares each filter on/off over the same train+validation dates with costs", "source": "backtest.run_research"}],
                        missing=miss, risks=["indicators overlap (EMA, VWAP, MACD, ADX all measure trend)"])


# ------------------------------------------------------- 4: options

class OptionsContractAnalyst(Agent):
    name = "options_contract_analyst"

    def run(self, ctx):
        c = ctx.candidate
        if c is None:
            return self.rep(ctx, {"status": "NOT_EVALUATED", "reason": "no entry candidate"}, "NO_CONTRACT")
        res = evaluate_contracts(ctx.chain, ctx.now, "call" if c["dir"] == 1 else "put", ctx.s)
        rec = "CONTRACT_FOUND" if res["status"] == "OK" else "NO_CONTRACT"
        return self.rep(ctx, res, rec,
                        missing=[] if res["status"] == "OK" else ["verified executable SPY option quotes"],
                        risks=res.get("warnings", []) + ([res["message"]] if res.get("message") else []))


# ------------------------------------------------- 5: volatility / events

class VolEventAnalyst(Agent):
    name = "volatility_event_analyst"

    def run(self, ctx):
        r, risks, miss = ctx.df.iloc[-1], [], []
        f = {"realized_vol_annualised": None if pd.isna(r.rv) else round(float(r.rv), 3), "vol_regime": r.vol_regime}
        v = ctx.vix
        if v is not None and len(v) > 12:
            lvl = float(v.close.iloc[-1])
            prior = v[v.index.date < v.index[-1].date()]
            ref = float(prior.close.iloc[-1]) if len(prior) else float(v.close.iloc[0])
            hr = lvl / float(v.close.iloc[-12]) - 1
            f["vix"] = {"level": round(lvl, 2), "vs_prior_close_pct": round((lvl / ref - 1) * 100, 1),
                        "last_hour_pct": round(hr * 100, 1), "source": "Yahoo ^VIX, unofficial", "as_of": str(v.index[-1])}
            if lvl > 25:
                risks.append(f"VIX {lvl:.1f} is elevated: wider swings likely (not a direction signal)")
            if hr > 0.05:
                risks.append("VIX jumped more than 5% in the last hour: volatility spike")
        else:
            miss.append("VIX data")
        reason = cal.blackout_reason(ctx.now, ctx.events)
        f["event_blackout"] = reason
        f["option_spread_widening"] = "not measurable without option quotes"
        miss.append("option spread/IV data")
        rec = "BLACKOUT" if reason else ("CAUTION" if risks else "OK")
        return self.rep(ctx, f, rec, evidence=[{"claim": "event list is user-maintained, not live", "source": "config/events.yaml"}],
                        missing=miss, risks=risks + ([reason] if reason else []))


# ------------------------------------------------------ 6: strategy dev

class StrategyDevelopmentAgent(Agent):
    name = "strategy_development_agent"

    def run(self, ctx):
        bt = (ctx.backtest or {}).get("strategies", {})
        reg, props = {}, []
        for n, cls in REGISTRY.items():
            st = cls()
            v = bt.get(n, {}).get("verdict", "NO_BACKTEST")
            reg[n] = {"version": st.spec.version, "label": st.label(), "hypothesis": st.spec.hypothesis, "verdict": v}
            if v == "FAILED_OOS":
                props.append(f"{n}: failed out of sample. Document the failure; redesign as a NEW version rather than retuning.")
            elif v == "INSUFFICIENT_SAMPLE":
                props.append(f"{n}: keep forward paper testing to accumulate trades; do not change parameters meanwhile.")
        return self.rep(ctx, {"registry": reg, "proposals_not_applied": props,
                              "policy": "Proposals never change live rules automatically. New versions need backtest, out-of-sample test and approval."},
                        "INFO", evidence=[{"claim": "strategy specs are versioned and parameter-hashed", "source": "strategies.py"}])


# ------------------------------------------------------- 7: backtesting

class BacktestAgent(Agent):
    name = "quantitative_backtesting_agent"

    def run(self, ctx):
        bt, c = ctx.backtest, ctx.candidate
        if not bt:
            return self.rep(ctx, {"evidence_status": "NO_BACKTEST"}, "INFO", missing=["backtest report"],
                            risks=["strategy has no historical evidence"])
        if c is None:
            return self.rep(ctx, {"evidence_status": "NOT_APPLICABLE", "options_research": bt["options_profitability_research"]["status"]}, "INFO")
        sr = (bt.get("strategies") or {}).get(c["strategy"])
        if not sr:
            return self.rep(ctx, {"evidence_status": "NO_BACKTEST"}, "INFO", missing=["backtest for " + c["strategy"]])
        o = sr["out_of_sample_reported_once"]
        f = {"evidence_status": sr["verdict"], "verdict_reason": sr["verdict_reason"], "out_of_sample": o,
             "train": sr["train"], "validation": sr["validation"], "folds_positive_fraction": sr.get("folds_positive_fraction"),
             "top_day_share_of_total_R": sr.get("top_day_share_of_total_R"), "data": bt["data"],
             "scope": bt["report_type"], "options_research": bt["options_profitability_research"]["status"]}
        rec = "VETO" if sr["verdict"] == "FAILED_OOS" else "INFO"
        return self.rep(ctx, f, rec, strategy_version=c["label"],
                        evidence=[{"claim": sr["verdict_reason"], "source": "latest backtest report"}],
                        risks=["underlying share results do NOT show option profitability"] + bt.get("warnings", []))


# ----------------------------------------------------- 8: adversarial

class AdversarialReviewer(Agent):
    name = "adversarial_strategy_reviewer"

    def run(self, ctx):
        c, rp = ctx.candidate, ctx.reports
        if c is None:
            return self.rep(ctx, {"vetoes": [], "warnings": []}, "NO_OBJECTION")
        r, s = ctx.df.iloc[-1], ctx.s
        vetoes, warns, contra = [], [], []
        dq = rp.get("data_quality_monitor")
        if dq is None or dq.recommendation != "OK":
            vetoes.append("data quality/safety check did not pass")
        if rp.get("five_minute_entry_analyst") and rp["five_minute_entry_analyst"].recommendation == "CONFLICT":
            vetoes.append("strategies disagree on direction")
        ve = rp.get("volatility_event_analyst")
        if ve and ve.recommendation == "BLACKOUT":
            vetoes.append("event blackout")
        bt = rp.get("quantitative_backtesting_agent")
        status = bt.findings.get("evidence_status") if bt else "NO_BACKTEST"
        if status == "FAILED_OOS":
            vetoes.append("strategy failed its out-of-sample test")
        elif status in ("INSUFFICIENT_SAMPLE", "NO_BACKTEST", "NOT_DEMONSTRATED"):
            warns.append(f"evidence status {status}: this is a forward test, not a proven edge")
        k = c["dir"]
        if c["strategy"] in CONTINUATION and r.h_dir != k:
            vetoes.append("continuation setup conflicts with the 1-hour regime")
        if c["strategy"] in REVERSAL and r.h_dir == -k and r.adx >= 25:
            vetoes.append("counter-trend reversal inside a strong 1-hour trend")
        risk_px = c["stop_atr"] * c["atr"]
        cost_frac = (2 * s.slippage_usd + 2 * s.commission_usd) / risk_px if risk_px > 0 else 9
        if cost_frac > 0.25:
            vetoes.append(f"round-trip costs are {cost_frac:.0%} of the 1R risk; edge likely erased")
        if pd.notna(r.vwap) and ((k == 1 and r.close < r.vwap) or (k == -1 and r.close > r.vwap)):
            contra.append("price is on the wrong side of VWAP for this direction")
        if (k == 1 and r.rsi > 72) or (k == -1 and r.rsi < 28):
            contra.append(f"RSI {r.rsi:.0f} is stretched against the entry")
        v = ve.findings.get("vix") if ve else None
        if v and ((k == 1 and v["last_hour_pct"] > 2) or (k == -1 and v["last_hour_pct"] < -2)):
            contra.append("VIX is moving against the direction over the last hour")
        top = (bt.findings.get("top_day_share_of_total_R") if bt else None)
        if top and top > 0.5:
            warns.append(f"{top:.0%} of the strategy's backtest profit came from one day")
        ent = rp.get("five_minute_entry_analyst")
        if ent and ent.findings.get("candidates") and len({x["strategy"] for x in ent.findings["candidates"]}) == 1 and len(ctx.df) < 78 * 30:
            warns.append("less than 30 days of history behind these statistics")
        return self.rep(ctx, {"vetoes": vetoes, "warnings": warns, "cost_fraction_of_1R": round(cost_frac, 3),
                              "questions_checked": ["contradicting evidence", "1h regime conflict", "sample size", "costs vs edge",
                                                    "out-of-sample survival", "single-session dependence", "data freshness"]},
                        "VETO" if vetoes else "NO_OBJECTION", contradictory=contra, risks=vetoes + warns)


# ----------------------------------------------------------- 9: risk

class RiskManagerAgent(Agent):
    name = "independent_risk_manager"

    def run(self, ctx):
        c = ctx.candidate
        if c is None:
            return self.rep(ctx, {"approved": False, "reason": "no candidate"}, "NOT_EVALUATED")
        k, risk = c["dir"], c["stop_atr"] * c["atr"]
        entry = c["close"]
        opt = None
        oc = ctx.reports.get("options_contract_analyst")
        if oc and oc.findings.get("status") == "OK":
            opt = oc.findings["candidates"][0]
        dq = ctx.reports.get("data_quality_monitor")
        ev = ctx.reports.get("volatility_event_analyst")
        v = RiskManager(ctx.s).evaluate(
            now=ctx.now, direction=k, entry=entry, stop=entry - k * risk, t1=entry + k * c["t1_r"] * risk,
            t2=entry + k * c["t2_r"] * risk,
            ledger=ctx.ledger, data_ok=bool(dq and dq.recommendation == "OK"),
            event_reason=cal.blackout_reason(ctx.now, ctx.events), kill=ctx.kill, option=opt)
        f = {"approved": v.approved, "rejection_reasons": v.reasons, "checks": v.checks, "max_loss_usd": v.max_loss_usd,
             "shares": v.shares, "contracts": v.contracts, "sizing_basis": v.sizing_basis, "ledger": ctx.ledger}
        return self.rep(ctx, f, "APPROVE" if v.approved else "REJECT", risks=v.reasons)


# ------------------------------------------------------- 11: performance

class PerformanceAnalyst(Agent):
    name = "performance_adaptation_analyst"

    def run(self, ctx):
        p = ctx.paper
        if p is None or len(p) == 0:
            return self.rep(ctx, {"paper_trades": 0}, "INFO", missing=["closed paper trades"],
                            risks=["no forward evidence yet"])
        m = metrics(p.rename(columns={}))
        f = {"paper_overall": m, "scope": "SIMULATED SPY share trades, not option fills"}
        if "regime" in p:
            f["by_regime"] = {k: round(float(g.net_R.mean()), 3) for k, g in p.groupby("regime")}
        p2 = p.copy()
        p2["hour"] = pd.to_datetime(p2["entry_time"], utc=True, errors="coerce").dt.tz_convert("America/New_York").dt.hour
        f["by_hour"] = {int(k): round(float(g.net_R.mean()), 3) for k, g in p2.groupby("hour") if pd.notna(k)}
        f["by_strategy"] = {k: {"trades": int(len(g)), "net_expectancy_R": round(float(g.net_R.mean()), 3)} for k, g in p.groupby("strategy")}
        drift, bt = [], (ctx.backtest or {}).get("strategies", {})
        for k, g in p.groupby("strategy"):
            exp = bt.get(k, {}).get("out_of_sample_reported_once", {}).get("net_expectancy_R")
            if exp is not None and len(g) >= 20:
                drift.append({"strategy": k, "backtest_oos_R": exp, "forward_paper_R": round(float(g.net_R.mean()), 3)})
        f["backtest_vs_forward"] = drift
        f["slippage_vs_assumption"] = "not measurable: paper fills are simulated"
        props = ["Any rule change must be a new strategy version, backtested, validated out of sample, and approved."]
        if m["trades"] < 30:
            props.append("Fewer than 30 closed trades: statistics are too thin to act on.")
        return self.rep(ctx, f, "INFO", risks=props)
