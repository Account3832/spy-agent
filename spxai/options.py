"""Option contract evaluation. Works ONLY with verified executable quotes.

Free quotes are indicative or delayed, so by default this module returns UNAVAILABLE.
It never invents a strike, price, or Greek, and never treats indicative/delayed/last-trade
prices as executable.
"""
from __future__ import annotations

import pandas as pd

from .config import Settings
from .providers import NO_OPTIONS_MSG

EXECUTABLE = "executable_realtime"
MULTIPLIER = 100  # SPY option contract multiplier


def evaluate_contracts(chain: pd.DataFrame | None, now: pd.Timestamp, side: str, s: Settings,
                       dte_days: int = 0, top: int = 3) -> dict:
    """side: 'call' or 'put'. Returns status UNAVAILABLE | NON_EXECUTABLE | NO_SUITABLE_CONTRACT | OK."""
    if chain is None or len(chain) == 0:
        return {"status": "UNAVAILABLE", "message": NO_OPTIONS_MSG, "candidates": []}
    classes = set(chain["quote_class"].dropna().unique())
    if EXECUTABLE not in classes:
        return {"status": "NON_EXECUTABLE",
                "message": f"Available quotes are {sorted(classes)}, which are not executable. Shown for research only; "
                           "no contract is recommended and no entry price is given.",
                "candidates": []}
    df = chain[(chain.quote_class == EXECUTABLE) & (chain.right == side)].copy()
    target_date = (now + pd.Timedelta(days=dte_days)).date()
    rej: dict[str, int] = {}

    def reject(mask, why):
        nonlocal df
        n = int(mask.sum())
        if n:
            rej[why] = rej.get(why, 0) + n
        df = df[~mask]

    reject(df.expiry != target_date, "wrong_expiry")
    reject(df.bid.isna() | df.ask.isna() | (df.bid <= 0), "no_bid")
    reject(df.ask < df.bid, "crossed_quote")
    ts = pd.to_datetime(df.quote_ts, utc=True, errors="coerce")
    age = (now.tz_convert("UTC") - ts).dt.total_seconds()
    reject(age.isna() | (age > s.max_quote_age_sec) | (age < -2), "stale_or_invalid_timestamp")
    mid = (df.bid + df.ask) / 2
    spread_pct = (df.ask - df.bid) / mid * 100
    reject(spread_pct > s.max_spread_pct, "spread_too_wide")
    reject(df.delta.isna(), "no_greeks")
    reject(df.volume.isna() | (df.volume < s.min_option_volume), "low_volume")
    reject(df.oi.isna() | (df.oi < s.min_option_oi), "low_open_interest")
    if df.empty:
        return {"status": "NO_SUITABLE_CONTRACT", "message": "Every contract failed a liquidity or freshness rule.",
                "rejected": rej, "candidates": []}
    mid = (df.bid + df.ask) / 2
    spread_pct = (df.ask - df.bid) / mid * 100
    # rank by closeness to target delta, then tighter spreads (never by cheapness)
    df = df.assign(mid=mid, spread_pct=spread_pct,
                   score=(df.delta.abs() - s.target_delta).abs() + spread_pct / 100.0).sort_values("score")
    out = []
    for _, r in df.head(top).iterrows():
        half_spread = (r.ask - r.bid) / 2 * MULTIPLIER
        out.append({"contract_id": r.contract_id, "strike": float(r.strike), "bid": float(r.bid), "ask": float(r.ask),
                    "mid": round(float(r.mid), 3), "spread_pct": round(float(r.spread_pct), 1), "delta": float(r.delta),
                    "theta": None if pd.isna(r.theta) else float(r.theta), "iv": None if pd.isna(r.iv) else float(r.iv),
                    "max_entry_price": float(r.ask),   # limit at the ask: no mid-price fantasy
                    "premium_per_contract_usd": round(float(r.ask) * MULTIPLIER, 2),
                    "est_costs_per_contract_usd": round(half_spread + s.option_commission_per_contract * 2, 2),
                    "quote_age_sec": round(float((now.tz_convert("UTC") - pd.to_datetime(r.quote_ts, utc=True)).total_seconds()), 1)})
    return {"status": "OK", "candidates": out, "rejected": rej,
            "warnings": ["Option P&L does not move one-for-one with the underlying: time decay, spread and implied "
                         "volatility changes apply. The underlying stop is NOT an option-premium stop.",
                         "Planned max loss on a long option is the premium paid."]}
