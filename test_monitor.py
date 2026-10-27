import os
from unittest import mock

import pandas as pd

from spxai import alerts, monitor
from spxai.alerts import AlertType
from spxai.config import Settings
from spxai.store import read_jsonl
from tests.helpers import tmpdir
from tests.test_system import FEATS, SIG_T


def _rows():
    return monitor.scan(Settings(), FEATS[FEATS.index <= SIG_T])


def test_scan_covers_all_twelve_and_flags_the_signal():
    rows = _rows()
    assert len(rows) == 13 and {r["kind"] for r in rows} == {"Live practice", "Research only"}
    tp = next(r for r in rows if r["strategy"] == "trend_pullback")
    assert tp["status"] == "SIGNAL" and tp["direction"] in ("LONG", "SHORT")
    k = 1 if tp["direction"] == "LONG" else -1
    assert (tp["entry_ref"] - tp["stop"]) * k > 0 and (tp["target1"] - tp["entry_ref"]) * k > 0
    assert (tp["target2"] - tp["entry_ref"]) * k > (tp["target1"] - tp["entry_ref"]) * k
    assert tp["shares"] >= 1 and rows[0]["status"] == "SIGNAL"
    assert all(r["status"] in monitor.STATUS_ORDER for r in rows)


def test_waiting_rows_explain_themselves():
    rows = monitor.scan(Settings(), FEATS[FEATS.index <= FEATS.index[200]])
    for r in rows:
        if r["status"] != "SIGNAL":
            assert r["note"] and r["waiting_for"]


def test_scan_empty_when_too_little_data():
    assert monitor.scan(Settings(), FEATS.head(10)) == []


def test_signal_text_has_levels_and_paper_wording():
    tp = next(r for r in _rows() if r["strategy"] == "trend_pullback")
    t = monitor.signal_text(tp)
    assert str(tp["stop"]) in t and str(tp["target1"]) in t and str(tp["target2"]) in t and "15:45" in t


def test_notify_dedups_and_respects_live_vs_research():
    base, rows = tmpdir(), _rows()
    sent = []
    fake = lambda kind, text, key=None, base=None, telegram=True: sent.append((kind, key, telegram)) or {"sent": True}
    monitor.notify_signals(rows, "NO TRADE", Settings(), base=base, send=fake)
    live = [x for x in sent if x[0] == AlertType.SIGNAL_NOT_TAKEN]
    assert live and all(x[2] for x in live)
    assert all(x[2] is False for x in sent if x[0] == AlertType.RESEARCH_SIGNAL)     # research: log only by default
    sent.clear()
    monitor.notify_signals(rows, "CALL", Settings(), base=base, send=fake)           # became a trade -> the entry alert covers it
    assert not [x for x in sent if x[0] == AlertType.SIGNAL_NOT_TAKEN]
    sent.clear()
    monitor.notify_signals(rows, "NO TRADE", Settings(notify_research_signals=True), base=base, send=fake)
    assert all(x[2] for x in sent if x[0] == AlertType.RESEARCH_SIGNAL)


def test_real_send_alert_dedup_and_telegram_rules():
    base = tmpdir()
    posts = []

    class R:
        ok = True
        status_code = 200

    def fake_post(url, json=None, timeout=0):
        posts.append(json["text"])
        return R()
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "C"}), mock.patch("requests.post", fake_post):
        a = alerts.send_alert(AlertType.ENTRY_TRIGGER, "enter now", key="k1", base=base)
        b = alerts.send_alert(AlertType.ENTRY_TRIGGER, "enter now", key="k1", base=base)
        c = alerts.send_alert(AlertType.SETUP_FORMING, "maybe soon", key="k2", base=base)
        d = alerts.send_alert(AlertType.RESEARCH_SIGNAL, "log only", key="k3", base=base, telegram=False)
    assert a["telegram"] == "delivered" and b["sent"] is False
    assert c["telegram"] == "not_sent_by_design" and d["telegram"] == "not_sent_by_design"
    assert len(posts) == 1 and "PAPER" in posts[0]
    assert len(read_jsonl(base / "alerts.jsonl", 50)) == 3


def test_not_configured_is_reported_and_token_never_stored():
    base = tmpdir()
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None); os.environ.pop("TELEGRAM_CHAT_ID", None)
        r = alerts.send_alert(AlertType.SYSTEM, "hello", key="z", base=base)
    assert r["telegram"] == "not_configured"
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "SECRET123", "TELEGRAM_CHAT_ID": "9"}), \
            mock.patch("requests.post", side_effect=RuntimeError("boom SECRET123")):
        r = alerts.send_alert(AlertType.SYSTEM, "hello2", key="y", base=base)
    assert r["telegram"].startswith("failed") and "SECRET123" not in str(r)


def test_find_chat_ids_reads_updates_and_hides_the_token():
    class R:
        ok = True
        status_code = 200
        def json(self):
            return {"result": [{"message": {"chat": {"id": 555}}}, {"message": {"chat": {"id": 555}}}, {"edited_message": {"chat": {"id": 7}}}]}
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "TOK"}), mock.patch("requests.get", return_value=R()):
        ids, st = alerts.find_chat_ids()
    assert ids == [7, 555] and st == "ok"
    bad = mock.Mock(ok=False, status_code=404)
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "TOK"}), mock.patch("requests.get", return_value=bad):
        assert alerts.find_chat_ids() == ([], "bad_token")
    empty = mock.Mock(ok=True, status_code=200, json=lambda: {"result": []})
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "TOK"}), mock.patch("requests.get", return_value=empty):
        assert alerts.find_chat_ids() == ([], "no_messages")


def test_token_is_cleaned_and_shape_never_reveals_it():
    tok = "1234567890:AAHsecretLETTERS_and-numbers123456789"
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": f'  "{tok}"\n ', "TELEGRAM_CHAT_ID": " 42 \n"}):
        assert alerts._token() == tok and alerts._chat() == "42" and alerts.telegram_enabled()
        shape = alerts.token_shape()
        assert "AAHsecret" not in shape and "LETTERS" not in shape and "1234567890" not in shape and "quote" in shape and "line breaks" in shape
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "https://t.me/mybot"}):
        sh = alerts.token_shape()
        assert "web link" in sh and "looks wrong" in sh
    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}):
        assert "missing" in alerts.token_shape()
