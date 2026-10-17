"""Live monitor: checks EVERY strategy (practice and research-only) against the newest completed 5-minute candle.

Used by the dashboard (Monitor tab) and by the 5-minute scheduled job, which writes monitor.json and sends Telegram
messages. Everything is PAPER ONLY: a "signal" is the rule firing, never an order.
"""
from __future__ import annotations

import math

import pandas as pd

from .config import Settings
from .strategies import REGISTRY

STATUS_ORDER = {"SIGNAL": 0, "FORMING": 1, "WAITING": 2, "OFF": 3}
FILTER_TEXT = {
    "htf": "the 1-hour trend does not agree",
    "vwap": "price is on the wrong side of the day's average price (VWAP)",
    "adx": "the trend is too weak (ADX under 20)",
    "rvol": "trading volume is below normal",
}


def _num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _blockers(inst, d: pd.DataFrame, s: Settings) -> list[str]:
    """Why a rule is not signalling on the last bar. Empty list = nothing is close (the raw trigger has not happened)."""
    longs, shorts, _ = inst.raw(d)
    lg, sh = bool(longs.fillna(False).iloc[-1]), bool(shorts.fillna(False).iloc[-1])
    if not (lg or sh):
        return []
    out = []
    for f in inst.filters:
        probe = type(inst)(filters=(f,))
        l2, s2 = probe._filter(d, longs.fillna(False), shorts.fillna(False))
        ok = bool(l2.iloc[-1]) if lg else bool(s2.iloc[-1])
        if not ok:
            out.append(FILTER_TEXT.get(f, f))
    tod = d.tod.iloc[-1]
    if not (s.entry_start <= tod <= s.entry_end):
        out.append(f"outside the entry window ({s.entry_start} to {s.entry_end})")
    return out


def scan(s: Settings, feats: pd.DataFrame, names=None) -> list[dict]:
    """One row per strategy for the LAST completed bar of `feats` (output of add_features)."""
    if feats is None or len(feats) < 50:
        return []
    live = set(s.enabled_strategies)
    names = list(names or (*s.enabled_strategies, *s.research_only_strategies))
    last = feats.iloc[-1]
    atr = _num(last.atr)
    risk_usd = s.account_equity * s.max_risk_pct / 100.0
    bar_close = feats.index[-1] + pd.Timedelta(minutes=5)
    rows = []
    for n in names:
        if n not in REGISTRY:
            continue
        inst = REGISTRY[n]()
        row = {"strategy": n, "kind": "Live practice" if n in live else "Research only", "status": "WAITING", "direction": "",
               "trigger": "", "entry_ref": None, "stop": None, "target1": None, "target2": None, "risk_per_share": None,
               "shares": None, "risk_usd": None, "note": "", "bar_close": str(bar_close), "waiting_for": inst.spec.trigger}
        try:
            sig = inst.signals(feats, entry_start=s.entry_start, entry_end=s.entry_end).iloc[-1]
        except Exception as e:  # noqa: BLE001
            row.update(status="OFF", note=f"could not be checked: {type(e).__name__}")
            rows.append(row)
            continue
        if int(sig["dir"]) != 0 and atr:
            k = int(sig["dir"])
            risk = inst.stop_atr * atr
            entry = float(last.close)
            row.update(status="SIGNAL", direction="LONG" if k == 1 else "SHORT", trigger=str(sig["trigger"]), entry_ref=round(entry, 2),
                       risk_per_share=round(risk, 2), stop=round(entry - k * risk, 2), target1=round(entry + k * inst.t1_r * risk, 2),
                       target2=round(entry + k * inst.t2_r * risk, 2), shares=max(int(risk_usd // risk), 0) if risk > 0 else 0,
                       risk_usd=round(risk_usd, 2), note="The rule fired on the newest completed candle. Entry would be the NEXT candle's open.")
        else:
            try:
                f = inst.forming(feats)
            except Exception:  # noqa: BLE001
                f = None
            blk = []
            try:
                blk = _blockers(inst, feats, s)
            except Exception:  # noqa: BLE001
                pass
            if blk:
                row.update(status="FORMING", note="The trigger candle appeared, but: " + "; ".join(blk) + ".")
            elif f:
                row.update(status="FORMING", note=f)
            else:
                row["note"] = "Nothing close to triggering."
        rows.append(row)
    rows.sort(key=lambda r: (STATUS_ORDER[r["status"]], r["kind"] != "Live practice", r["strategy"]))
    return rows


def signal_text(r: dict, symbol: str = "SPY") -> str:
    """Plain wording for a Telegram message about a firing rule."""
    side = "BUY (go long)" if r["direction"] == "LONG" else "SELL SHORT"
    return (f"{side} {symbol}: {r['strategy']} just signalled ({r['kind'].lower()}).\n"
            f"Entry: next 5-minute candle open (about {r['entry_ref']}).\n"
            f"Stop (exit if wrong): {r['stop']}\n"
            f"Target 1: {r['target1']} (sell half, then move stop to entry)\n"
            f"Target 2: {r['target2']} (sell the rest)\n"
            f"Size: about {r['shares']} shares risks about ${r['risk_usd']:.0f}.\n"
            f"Get out by 15:45 ET at the latest.")


def notify_signals(rows: list[dict], decision: str, s: Settings, base=None, symbol: str = "SPY", send=None) -> list[dict]:
    """Send alerts (Telegram when configured) for rules that fired. Duplicates are suppressed per strategy and candle.
    Live strategies that fired but did NOT become a practice trade get a 'not taken' message. Research-only signals are
    recorded and sent only when s.notify_research_signals is true."""
    from .alerts import AlertType, send_alert
    send = send or send_alert
    out = []
    for r in rows:
        if r["status"] != "SIGNAL":
            continue
        key = f"sig-{r['strategy']}-{r['bar_close']}"
        if r["kind"] == "Live practice":
            if decision in ("CALL", "PUT"):
                continue  # the normal entry alert already covers it
            out.append(send(AlertType.SIGNAL_NOT_TAKEN,
                            "SIGNAL, but the safety checks did not allow a practice trade.\n" + signal_text(r, symbol)
                            + "\nSee the Today tab for the reasons.", key=key, base=base))
        elif getattr(s, "notify_research_signals", False):
            out.append(send(AlertType.RESEARCH_SIGNAL,
                            "RESEARCH ONLY. This rule has not passed the fair test and is not a practice trade.\n" + signal_text(r, symbol),
                            key=key, base=base))
        else:  # still keep a record in the in-app log
            out.append(send(AlertType.RESEARCH_SIGNAL, "RESEARCH ONLY. " + signal_text(r, symbol), key=key, base=base, telegram=False))
    return out
