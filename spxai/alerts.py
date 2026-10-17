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
    SIGNAL_NOT_TAKEN = "SIGNAL_NOT_TAKEN"
    RESEARCH_SIGNAL = "RESEARCH_SIGNAL"
    SYSTEM = "SYSTEM"


# Noisy "maybe soon" messages are saved in the app but not pushed to your phone unless SPXAI_TELEGRAM_FORMING=1.
TELEGRAM_SKIP = {"SETUP_FORMING", "SETUP_INVALIDATED"}


def _clean(v: str | None) -> str:
    """Removes spaces, line breaks and wrapping quotes that sneak in when a secret is pasted."""
    return (v or "").strip().strip("\"'").strip().replace("\n", "").replace("\r", "").replace(" ", "")


def _token() -> str:
    return _clean(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _chat() -> str:
    return _clean(os.environ.get("TELEGRAM_CHAT_ID"))


def token_shape() -> str:
    """Describes the saved token WITHOUT revealing it, to help fix a bad paste."""
    raw = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    if not raw:
        return "token secret is empty or missing"
    t = _clean(raw)
    head, _, tail = t.partition(":")
    notes = [f"length {len(t)} (a real token is about 45 to 50)", "has a colon: " + ("yes" if ":" in t else "NO"),
             f"digits before the colon: {len(head)} ({'ok' if head.isdigit() and 8 <= len(head) <= 12 else 'looks wrong, expected 8 to 12 digits'})",
             f"characters after the colon: {len(tail)}"]
    if raw != raw.strip() or any(c in raw for c in " \n\r"):
        notes.append("the pasted secret had spaces or line breaks (now ignored)")
    if raw.strip()[:1] in ("\"", "'") or raw.strip()[-1:] in ("\"", "'"):
        notes.append("the pasted secret had quote marks around it (now ignored)")
    if t.lower().startswith("bot"):
        notes.append("it starts with the word 'bot', which is not part of the token")
    if "/" in t or "http" in t.lower():
        notes.append("it looks like a web link, not a token")
    if not head.isdigit() and head:
        notes.append("the part before the colon should be only digits")
    return "; ".join(notes)


def telegram_enabled() -> bool:
    return bool(_token() and _chat())


def send_telegram(text: str) -> str:
    """Sends one message. Returns 'delivered', 'not_configured', or a short error label. Never raises, never logs the token."""
    tok, chat = _token(), _chat()
    if not (tok and chat):
        return "not_configured"
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text}, timeout=10)
        return "delivered" if r.ok else f"http_{r.status_code}"
    except Exception as e:  # noqa: BLE001
        return f"failed: {type(e).__name__}"


def find_chat_ids() -> tuple[list[int], str]:
    """Asks Telegram which chats have messaged the bot. Returns (ids, status). Never prints or returns the token."""
    tok = _token()
    if not tok:
        return [], "no_token"
    try:
        import requests
        r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=10)
        if r.status_code in (401, 404):
            return [], "bad_token"
        if not r.ok:
            return [], f"http_{r.status_code}"
        ids = set()
        for u in r.json().get("result", []):
            for key in ("message", "edited_message", "channel_post", "my_chat_member"):
                chat = (u.get(key) or {}).get("chat") or {}
                if "id" in chat:
                    ids.add(int(chat["id"]))
        return sorted(ids), "ok" if ids else "no_messages"
    except Exception as e:  # noqa: BLE001
        return [], f"failed: {type(e).__name__}"


def send_alert(kind: AlertType, text: str, key: str | None = None, base: Path | None = None, telegram: bool = True) -> dict:
    """Idempotent: an alert with the same key is not sent twice. Telegram failure never raises."""
    base = base or state_dir()
    path = base / "alerts.jsonl"
    k = key or hashlib.sha1(f"{kind.value}|{text}".encode()).hexdigest()[:12]
    if any(a.get("key") == k for a in read_jsonl(path, last_n=300)):
        return {"sent": False, "reason": "duplicate"}
    msg = PREFIX + text
    rec = {"ts": str(pd.Timestamp.now(tz=TZ)), "type": kind.value, "message": msg, "key": k, "telegram": "not_configured"}
    if telegram and (kind.value not in TELEGRAM_SKIP or os.environ.get("SPXAI_TELEGRAM_FORMING") == "1"):
        rec["telegram"] = send_telegram(f"{kind.value}\n{msg}")
    elif not telegram_enabled():
        rec["telegram"] = "not_configured"
    else:
        rec["telegram"] = "not_sent_by_design"
    append_jsonl(path, rec)
    return {"sent": True, **rec}
