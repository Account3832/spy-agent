"""Deterministic decision engine. Language models (or free text of any kind) never decide.

Bounded workflow: ONE pass of analysts, ONE adversarial review, ONE risk check, then a fixed
rule list. Disagreements are recorded. Missing evidence returns NO TRADE.
"""
from __future__ import annotations

import pandas as pd

from . import calendar as cal
from .agents import (AdversarialReviewer, BacktestAgent, Ctx, DataQualityMonitor, EntryAnalyst, IndicatorResearcher,
                     OptionsContractAnalyst, PerformanceAnalyst, RegimeAnalyst, RiskManagerAgent,
                     StrategyDevelopmentAgent, VolEventAnalyst, run_agent)

WORKFLOW = [DataQualityMonitor, RegimeAnalyst, VolEventAnalyst, EntryAnalyst, IndicatorResearcher,
            StrategyDevelopmentAgent, BacktestAgent, OptionsContractAnalyst, AdversarialReviewer,
            RiskManagerAgent, PerformanceAnalyst]


def run_workflow(ctx: Ctx) -> dict:
    for cls in WORKFLOW:
        a = cls()
        rep = run_agent(a, ctx)
        ctx.reports[a.name] = rep
        if a.name == "five_minute_entry_analyst":
            cands = rep.findings.get("candidates") or []
            ctx.candidate = cands[0] if cands and rep.recommendation in ("LONG", "SHORT") else None
    return ctx.reports


def _disagreements(ctx: Ctx) -> list[str]:
    out, rp = [], ctx.reports
    c = ctx.candidate
    if c is not None:
        reg = rp["market_regime_analyst"].recommendation
        want = "LONG" if c["dir"] == 1 else "SHORT"
        if reg not in ("NEUTRAL", want):
            out.append(f"regime analyst says {reg} but entry analyst says {want}")
        if rp["adversarial_strategy_reviewer"].recommendation == "VETO":
            out.append("adversarial reviewer vetoes the entry analyst's setup")
        if rp["independent_risk_manager"].recommendation == "REJECT":
            out.append("risk manager rejects the entry analyst's setup")
        if rp["quantitative_backtesting_agent"].recommendation == "VETO":
            out.append("backtest evidence contradicts the setup")
    for a in rp.values():
        if a.error:
            out.append(f"{a.agent} failed ({a.error}); treated as missing evidence")
    return out


def decide(ctx: Ctx) -> dict:
    """Fixed rule list. Returns the complete trade plan or a NO TRADE explanation."""
    if not ctx.reports:
        run_workflow(ctx)
    rp, s, now = ctx.reports, ctx.s, ctx.now
    c = ctx.candidate
    failed: list[dict] = []

    def req(name, ok, detail):
        if not ok:
            failed.append({"requirement": name, "detail": detail})

    dq = rp["data_quality_monitor"]
    req("1. data valid and fresh", dq.recommendation == "OK", "; ".join(dq.risk_concerns) or "data check failed")
    req("2/3. setup confirmed and entry trigger occurred", c is not None,
        "no strategy trigger on the latest completed 5-minute bar" if c is None else "")
    rev, risk = rp["adversarial_strategy_reviewer"], rp["independent_risk_manager"]
    opt = rp["options_contract_analyst"]
    option_ok = opt.findings.get("status") == "OK"
    if c is not None:
        req("7. no disqualifying condition", rev.recommendation != "VETO", "; ".join(rev.findings.get("vetoes", [])))
        req("5/6. reward:risk acceptable and risk manager approves", risk.recommendation == "APPROVE",
            "; ".join(risk.findings.get("rejection_reasons", [])))
        bt = rp["quantitative_backtesting_agent"]
        status = bt.findings.get("evidence_status", "NO_BACKTEST")
        if not s.paper_allow_unvalidated:
            req("evidence gate (paper_allow_unvalidated=false)", status == "UNDERLYING_EDGE_SUPPORTED",
                f"evidence status is {status}")
        if opt.findings.get("status") in ("OK",):
            pass  # contract verified
    disagreements = _disagreements(ctx)
    base = {"timestamp": str(now), "timezone": "America/New_York", "data_status": ctx.data_status, "data_source": ctx.source,
            "instrument": s.symbol, "agent_disagreements": disagreements,
            "regime": rp["market_regime_analyst"].findings.get("classification"),
            "mode": "PAPER ONLY - no order is ever sent"}
    next_bar = (ctx.df.index[-1] + pd.Timedelta(minutes=10)).strftime("%H:%M")
    if failed:
        forming = rp["five_minute_entry_analyst"].findings.get("setups_forming", [])
        return {**base, "decision": "NO TRADE", "failed_requirements": failed,
                "setups_forming": forming,
                "re_evaluate_when": f"after the next 5-minute bar closes (about {next_bar} ET); "
                                    + ("or when the data feed recovers" if dq.recommendation != "OK" else "")}
    k = c["dir"]
    risk_px = c["stop_atr"] * c["atr"]
    entry = c["close"]
    stop, t1, t2 = entry - k * risk_px, entry + k * c["t1_r"] * risk_px, entry + k * c["t2_r"] * risk_px
    exit_ts = cal.mandatory_exit_ts(now.date(), s.mandatory_exit)
    close_ts = pd.Timestamp(c["signal_time"]) + pd.Timedelta(minutes=5)
    sh = risk.findings.get("shares")
    bt_rep = rp["quantitative_backtesting_agent"]
    ev_status = bt_rep.findings.get("evidence_status", "NO_BACKTEST")
    contract = opt.findings["candidates"][0] if option_ok else None
    spy_cost = (2 * s.slippage_usd + 2 * s.commission_usd) * (sh or 0)
    return {**base,
            "decision": "CALL" if k == 1 else "PUT",
            "decision_meaning": "bullish thesis (CALL / long)" if k == 1 else "bearish thesis (PUT / short)",
            "execution_basis": "OPTION CONTRACT (verified executable quote)" if option_ok else
            "SIMULATED SPY SHARES (no verified executable option quote; option P&L is NOT computed)",
            "strategy": c["strategy"], "strategy_version": c["version"], "strategy_label": c["label"],
            "evidence_status": ev_status,
            "evidence_warning": None if ev_status == "UNDERLYING_EDGE_SUPPORTED" else
            "UNVALIDATED: forward paper test only. Backtests have not shown a reliable edge.",
            "underlying_entry_trigger": c["trigger"], "confirmation_requirements": c["confirmation"],
            "signal_bar_start": c["signal_time"], "entry_rule": "paper entry at the NEXT 5-minute bar's open",
            "option_contract": contract,
            "option_contract_note": None if option_ok else rp["options_contract_analyst"].findings.get("message"),
            "proposed_max_entry_price": contract["max_entry_price"] if option_ok else round(entry + k * 0.25 * c["atr"], 2),
            "underlying_reference_price": round(entry, 2),
            "stop_loss_invalidation": {"underlying_stop": round(stop, 2), "rule": c["invalidation"],
                                       "note": "Underlying stop is NOT an option-premium stop."},
            "target_1": round(t1, 2), "target_2": round(t2, 2),
            "target_logic": "half off at T1, stop to breakeven, remainder to T2",
            "time_exit": exit_ts.strftime("%H:%M ET"),
            "position_size": {"shares": sh, "contracts": risk.findings.get("contracts")},
            "max_planned_loss_usd": risk.findings["max_loss_usd"],
            "estimated_transaction_costs_usd": round(spy_cost, 2) if not option_ok else contract["est_costs_per_contract_usd"],
            "reward_to_risk_t1": risk.findings["checks"].get("reward_risk_t1"),
            "reward_to_risk_blended": risk.findings["checks"].get("reward_risk_blended"),
            "supporting_evidence": [c["trigger"], f"1h trend = {c['h_dir']}, regime = {c['regime']}",
                                    bt_rep.findings.get("verdict_reason", "no backtest available")],
            "contradictory_evidence": rev.contradictory_evidence, "reviewer_warnings": rev.findings.get("warnings", []),
            "cancel_conditions": [c["invalidation"], "price trades through the stop before entry",
                                  "1-hour trend flips against the trade", "data feed goes stale", "kill switch / daily lockout"],
            "decision_expires": str(close_ts + pd.Timedelta(minutes=s.decision_ttl_minutes)),
            "paper_position": {"label": c["label"], "strategy": c["strategy"], "dir": k, "signal_time": c["signal_time"],
                               "atr": c["atr"], "stop_atr": c["stop_atr"], "t1_r": c["t1_r"], "t2_r": c["t2_r"],
                               "max_hold_bars": c["max_hold_bars"], "shares": sh, "regime": c["regime"],
                               "instrument": "SPY shares (simulated)" if not option_ok else "SPY option (simulated fill)",
                               "evidence_status": ev_status}}
