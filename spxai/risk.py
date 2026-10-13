"""Independent risk manager. Pure rules; no agent or model can override it.

No martingale: position size never grows after a loss. Losses can only LOCK OUT trading.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from . import calendar as cal
from .config import Settings


@dataclass
class RiskVerdict:
    approved: bool
    reasons: list = field(default_factory=list)       # reasons for rejection
    checks: dict = field(default_factory=dict)
    max_loss_usd: float = 0.0
    shares: int | None = None
    contracts: int | None = None
    sizing_basis: str = ""


class RiskManager:
    def __init__(self, s: Settings):
        self.s = s

    def evaluate(self, *, now: pd.Timestamp, direction: int, entry: float, stop: float, t1: float, t2: float | None = None,
                 ledger: dict, data_ok: bool, event_reason: str | None, kill: tuple[bool, str],
                 option: dict | None = None) -> RiskVerdict:
        s = self.s
        v = RiskVerdict(False)
        v.max_loss_usd = round(s.account_equity * s.max_risk_pct / 100, 2)
        chk = v.checks

        def rule(name, ok, why):
            chk[name] = bool(ok)
            if not ok:
                v.reasons.append(why)

        rule("kill_switch", not kill[0], f"kill switch active: {kill[1]}")
        rule("data_valid", data_ok, "data quality check failed or data is stale")
        rule("event_blackout", event_reason is None, event_reason or "")
        lock = s.account_equity * s.max_daily_loss_pct / 100
        rule("daily_lockout", ledger.get("realized_today_usd", 0.0) > -lock,
             f"daily loss limit reached (${-ledger.get('realized_today_usd', 0.0):.0f} lost, limit ${lock:.0f})")
        rule("max_trades", ledger.get("trades_today", 0) < s.max_trades_per_day, "max trades per day reached")
        rule("max_concurrent", ledger.get("open_count", 0) < s.max_concurrent, "max concurrent positions reached")
        tod = now.strftime("%H:%M")
        rule("entry_window", s.entry_start <= tod <= s.entry_end, f"outside entry window {s.entry_start}-{s.entry_end}")
        exit_ts = cal.mandatory_exit_ts(now.date(), s.mandatory_exit)
        rule("time_to_exit", (exit_ts - now).total_seconds() >= 20 * 60, "less than 20 minutes before mandatory exit")
        risk_ps = abs(entry - stop)
        rr1 = round(abs(t1 - entry) / risk_ps, 4) if risk_ps > 0 else 0.0
        rr2 = round(abs((t2 if t2 is not None else t1) - entry) / risk_ps, 4) if risk_ps > 0 else 0.0
        rr = round((rr1 + rr2) / 2, 4)  # half exits at T1, half at T2 (plan estimate, not a promise)
        chk["reward_risk_t1"], chk["reward_risk_blended"] = round(rr1, 2), round(rr, 2)
        rule("min_reward_risk", rr + 1e-9 >= s.min_reward_risk,
             f"blended reward:risk is {rr:.2f} (T1 {rr1:.2f}), below the {s.min_reward_risk} minimum")
        if option is None:
            slip = s.slippage_usd
            per_share = risk_ps + 2 * slip + 2 * s.commission_usd
            sh = math.floor(v.max_loss_usd / per_share) if per_share > 0 else 0
            v.shares, v.sizing_basis = sh, "SPY shares, SIMULATED (no verified option quote)"
            rule("size_at_least_one", sh >= 1, "risk budget too small for one share at this stop distance")
        else:
            cost = option["premium_per_contract_usd"] + option["est_costs_per_contract_usd"]
            by_risk = math.floor(v.max_loss_usd / cost) if cost > 0 else 0
            by_exposure = math.floor(s.account_equity * s.max_premium_exposure_pct / 100 / cost) if cost > 0 else 0
            v.contracts = min(by_risk, by_exposure)
            v.sizing_basis = "long option, max loss = premium + costs"
            rule("spread_ok", option["spread_pct"] <= s.max_spread_pct, "option spread too wide")
            rule("quote_fresh", option["quote_age_sec"] <= s.max_quote_age_sec, "option quote is stale")
            rule("size_at_least_one", v.contracts >= 1, "one contract exceeds the risk or exposure limit")
        v.approved = not v.reasons
        return v
