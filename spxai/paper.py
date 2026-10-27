"""Paper-trading ledger. Simulated fills only; nothing here talks to a broker.

Positions are re-resolved from the stored bars on every update using the SAME conservative
rules as the backtester (resolve_trade), so the process is idempotent and restart-safe.
Without verified executable option quotes, paper trades are SPY SHARE trades labelled SIMULATED.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from . import calendar as cal
from .backtest import resolve_trade
from .config import Settings, TZ, state_dir
from .store import read_json, write_json

CLOSED_COLS = ["id", "strategy", "label", "dir", "signal_time", "entry_time", "exit_time", "entry", "risk_per_share",
               "shares", "net_R", "gross_R", "pnl_usd", "exit_reason", "regime", "instrument", "evidence_status"]


def trade_id(label: str, direction: int, signal_ts) -> str:
    return hashlib.sha1(f"{label}|{direction}|{pd.Timestamp(signal_ts).isoformat()}".encode()).hexdigest()[:12]


class PaperLedger:
    def __init__(self, s: Settings, base: Path | None = None):
        self.s, self.base = s, base or state_dir()
        self.open_path, self.closed_path = self.base / "open_positions.json", self.base / "paper_trades.csv"

    def positions(self) -> list[dict]:
        return read_json(self.open_path, [])

    def closed(self) -> pd.DataFrame:
        if not self.closed_path.exists():
            return pd.DataFrame(columns=CLOSED_COLS)
        return pd.read_csv(self.closed_path)

    def open_position(self, plan: dict) -> bool:
        """Register a paper position. Idempotent on (strategy label, direction, signal bar)."""
        tid = trade_id(plan["label"], plan["dir"], plan["signal_time"])
        if any(p["id"] == tid for p in self.positions()) or (len(self.closed()) and tid in set(self.closed()["id"])):
            return False
        pos = {**plan, "id": tid, "created": str(pd.Timestamp.now(tz=TZ)), "t1_alerted": False, "filled_alerted": False}
        write_json(self.open_path, self.positions() + [pos])
        return True

    def update(self, bars: pd.DataFrame, now: pd.Timestamp) -> list[dict]:
        """bars must contain feature columns (atr, tod, date) and only COMPLETED bars."""
        events, keep = [], []
        o, h, l, c = (bars[x].values for x in ("open", "high", "low", "close"))
        tod, date, idx = bars.tod.values, bars.date.values, bars.index
        for p in self.positions():
            sig_ts = pd.Timestamp(p["signal_time"])
            if sig_ts not in idx:
                keep.append(p)
                continue
            i = idx.get_loc(sig_ts)
            if i + 1 >= len(bars) or date[i + 1] != date[i]:
                if i + 1 >= len(bars):  # next bar not complete yet
                    keep.append(p)
                    continue
                self._finalize_cancel(p, events, "next_bar_in_new_session")
                continue
            k, slip = int(p["dir"]), self.s.slippage_usd
            e_ref = o[i + 1]
            entry = e_ref + k * slip
            risk = p["stop_atr"] * p["atr"]
            stop, t1, t2 = entry - k * risk, entry + k * p["t1_r"] * risk, entry + k * p["t2_r"] * risk
            if p.get("plan_mode") == "chart":                  # stop and targets come from the chart, fixed at signal time
                stop, t1, t2 = float(p["stop_price"]), float(p["t1_price"]), float(p["t2_price"])
                risk = k * (entry - stop)
                if risk <= 0.2 * p["atr"] or k * (t1 - entry) <= 0:     # price gapped past the plan before the fill
                    self._finalize_cancel(p, events, "price opened beyond the chart-based plan")
                    continue
            xt = cal.mandatory_exit_ts(date[i], self.s.mandatory_exit).strftime("%H:%M")
            later_day = date[-1] != date[i]
            res = resolve_trade(o, h, l, c, tod, date, i + 1, k, e_ref, entry, risk, stop, t1, t2, slip, xt,
                                p["max_hold_bars"], data_end_closes=later_day)
            if not p.get("filled_alerted"):
                events.append({"type": "ENTRY_FILLED", "id": p["id"], "label": p["label"], "entry": round(entry, 2),
                               "stop": round(stop, 2), "t1": round(t1, 2), "t2": round(t2, 2)})
                p["filled_alerted"] = True
            if not res["halves_open"][1] and not p.get("t1_alerted"):
                events.append({"type": "TARGET_REACHED", "id": p["id"], "label": p["label"], "t1": round(t1, 2)})
                p["t1_alerted"] = True
            if not res["closed"]:
                keep.append(p)
                continue
            sh = int(p["shares"] or 0)
            net_px = res["net"] - 2 * self.s.commission_usd
            row = {"id": p["id"], "strategy": p["strategy"], "label": p["label"], "dir": k, "signal_time": p["signal_time"],
                   "entry_time": str(idx[i + 1]), "exit_time": str(idx[res["last_j"]]), "entry": round(entry, 3),
                   "risk_per_share": round(risk, 3), "shares": sh, "net_R": round(net_px / risk, 3),
                   "gross_R": round(res["gross"] / risk, 3), "pnl_usd": round(net_px * sh, 2),
                   "exit_reason": f"{res['reasons'].get(1, '')}|{res['reasons'].get(2, '')}", "regime": p.get("regime"),
                   "instrument": p.get("instrument", "SPY shares (simulated)"), "evidence_status": p.get("evidence_status", "")}
            self._append_closed(row)
            kind = "STOP" if "stop" in row["exit_reason"] and "BE" not in row["exit_reason"] else (
                "TIME" if "time" in row["exit_reason"] or "maxhold" in row["exit_reason"] else "TARGET")
            events.append({"type": "EXIT_" + kind, "id": p["id"], "label": p["label"], "pnl_usd": row["pnl_usd"],
                           "net_R": row["net_R"], "reason": row["exit_reason"]})
        write_json(self.open_path, keep)
        return events

    def _finalize_cancel(self, p, events, why):
        events.append({"type": "CANCELLED", "id": p["id"], "label": p["label"], "reason": why})

    def _append_closed(self, row: dict):
        df = pd.concat([self.closed(), pd.DataFrame([row])], ignore_index=True)
        df = df.drop_duplicates(subset="id", keep="last")
        tmp = self.closed_path.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(self.closed_path)

    def summary(self, now: pd.Timestamp) -> dict:
        today = now.date().isoformat()
        cl = self.closed()
        today_cl = cl[cl["exit_time"].astype(str).str[:10] == today] if len(cl) else cl
        opens = self.positions()
        opened_today = sum(1 for p in opens if str(p["signal_time"])[:10] == today)
        return {"trades_today": int(len(today_cl) + opened_today), "open_count": len(opens),
                "realized_today_usd": float(today_cl["pnl_usd"].sum()) if len(today_cl) else 0.0}
