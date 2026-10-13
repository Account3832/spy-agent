"""In-app (JSONL) alerts plus optional Telegram. Every alert states it is simulated/paper."""
from __future__ import annotations

import hashlib
import os
from enum import Enum
from pathlib import Path

import pandas as pd

from .config import TZ, state_dir
from .store import append_jsonl, read_jsonl

PREFIX = "[PAPER / SIMULATED - not an order, nothing was sent to a broker] "


class AlertType(str, Enum):
    SETUP_FORMING = "SETUP_FORMING"
    ENTRY_TRIGGER = "ENTRY_TRIGGER_CONFIRMED"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    TARGET_REACHED = "TARGET_REACHED"
    STOP_OR_RISK_LIMIT = "STOP_OR_RISK_LIMIT_REACHED"
    MANDATORY_EXIT = "MANDATORY_TIME_EXIT"
    FEED_DISCONNECTED = "DATA_FEED_DISCONNECTED"
    STALE_QUOTE = "STALE_QUOTE"
    DAILY_LOCKOUT = "DAILY_LOCKOUT_ACTIVATED"
    SYSTEM = "SYSTEM"


def send_alert(kind: AlertType, text: str, key: str | None = None, base: Path | None = None) -> dict:
    """Idempotent: an alert with the same key is not sent twice. Telegram failure never raises."""
    base = base or state_dir()
    path = base / "alerts.jsonl"
    k = key or hashlib.sha1(f"{kind.value}|{text}".encode()).hexdigest()[:12]
    if any(a.get("key") == k for a in read_jsonl(path, last_n=300)):
        return {"sent": False, "reason": "duplicate"}
    msg = PREFIX + text
    rec = {"ts": str(pd.Timestamp.now(tz=TZ)), "type": kind.value, "message": msg, "key": k, "telegram": "not_configured"}
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            import requests
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": chat, "text": f"{kind.value}\n{msg}"}, timeout=10)
            rec["telegram"] = "delivered" if r.ok else f"http_{r.status_code}"
        except Exception as e:  # noqa: BLE001
            rec["telegram"] = f"failed: {type(e).__name__}"
    append_jsonl(path, rec)
    return {"sent": True, **rec}
