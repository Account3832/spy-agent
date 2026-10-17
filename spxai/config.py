"""Settings, $0 cost guard inputs, and the global kill switch."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

TZ = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    """Where runtime state lives. In GitHub Actions this points at the `data` branch checkout."""
    p = Path(os.environ.get("SPXAI_STATE_DIR", ROOT / "state"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_ny() -> pd.Timestamp:
    return pd.Timestamp.now(tz=TZ)


@dataclass
class Settings:
    # instrument
    symbol: str = "SPY"              # the ONLY instrument: SPY shares / SPY options
    primary_source: str = "alpaca_iex"   # alpaca_iex | yahoo ; falls back to yahoo if keys are missing/failing
    vix_symbol: str = "^VIX"        # context only (Yahoo, unofficial)
    # data budget: hard $0
    data_budget_usd: float = 0.0
    allow_paid_providers: bool = False
    # account assumptions (configurable, not universal)
    account_equity: float = 25000.0
    max_risk_pct: float = 0.5            # % of equity risked per trade
    max_daily_loss_pct: float = 2.0
    max_trades_per_day: int = 3
    max_concurrent: int = 1
    max_premium_exposure_pct: float = 1.0
    min_reward_risk: float = 1.5
    max_spread_pct: float = 12.0         # option spread as % of mid
    max_quote_age_sec: int = 20          # option quote freshness
    max_bar_age_sec: int = 720           # completed 5m bar must be this fresh while market open
    mandatory_exit: str = "15:45"
    entry_start: str = "09:45"           # bar START time, earliest signal bar
    entry_end: str = "14:55"             # bar START time, latest signal bar
    # underlying research costs in US dollars PER SHARE of SPY, per side
    slippage_usd: float = 0.02
    commission_usd: float = 0.0
    # modes
    mode: str = "paper"                  # live execution does not exist in this codebase
    paper_allow_unvalidated: bool = True  # lets forward paper testing gather evidence
    min_trades_for_claim: int = 100
    enabled_strategies: tuple = ("trend_pullback", "orb_retest", "failed_breakout",
                                 "range_reversion", "vol_expansion")
    # tested and reported, but NEVER used for live practice trades until they pass the fair test and are promoted here
    research_only_strategies: tuple = ("orb_breakout", "vwap_reclaim", "gap_fade", "prior_day_break", "steady_scalper")
    notify_research_signals: bool = False  # True = also push research-only signals to Telegram (noisy)
    # option selection
    target_delta: float = 0.35
    min_option_volume: int = 50
    min_option_oi: int = 100
    option_commission_per_contract: float = 0.65
    decision_ttl_minutes: int = 10


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    s = Settings()
    p = Path(path) if path else ROOT / "config" / "settings.yaml"
    if p.exists():
        import yaml
        data = yaml.safe_load(p.read_text()) or {}
        names = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, tuple(v) if isinstance(v, list) else v)
    for f in fields(Settings):  # env overrides: SPXAI_<NAME>
        raw = os.environ.get("SPXAI_" + f.name.upper())
        if raw is not None:
            cur = getattr(s, f.name)
            if isinstance(cur, bool):
                setattr(s, f.name, raw.lower() in ("1", "true", "yes"))
            elif isinstance(cur, int):
                setattr(s, f.name, int(raw))
            elif isinstance(cur, float):
                setattr(s, f.name, float(raw))
            elif isinstance(cur, str):
                setattr(s, f.name, raw)
    if s.mode != "paper":
        raise ValueError("Only mode='paper' is supported. Live execution is intentionally not implemented.")
    return s


def kill_switch(state: Path | None = None) -> tuple[bool, str]:
    """Global pause/kill switch: env var, KILL_SWITCH file, or control.json {"paused": true}."""
    if os.environ.get("SPXAI_KILL", "").lower() in ("1", "true", "yes"):
        return True, "SPXAI_KILL environment variable is set"
    sd = state or state_dir()
    if (sd / "KILL_SWITCH").exists():
        return True, "state/KILL_SWITCH file exists"
    ctl = sd / "control.json"
    if ctl.exists():
        import json
        try:
            if json.loads(ctl.read_text()).get("paused"):
                return True, "control.json paused=true"
        except Exception:
            return True, "control.json unreadable, failing safe"
    return False, ""
