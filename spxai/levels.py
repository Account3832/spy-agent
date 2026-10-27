"""Classic chart-technician toolkit: swing points, trend structure on several time scales ("trends within trends"),
support/resistance zones, trendlines and "room to run" checks. Pure pandas/numpy. Shared by the SPY and crypto projects.

Everything is CAUSAL: a swing point at bar i is only known (confirmed) `right` bars later, and every
number at time t uses only candles that had closed by t. Nothing here places orders; it describes the chart.

Method (all classic, nothing tuned on data):
  * Swing high/low  = the highest high (lowest low) among `left` bars before and `right` bars after it.
  * Trend structure = Dow theory. Higher highs + higher lows = UP; lower highs + lower lows = DOWN; otherwise RANGE.
                      A close beyond the last swing low (in an uptrend) / high (in a downtrend) marks the structure BROKEN.
  * Several scales  = daily -> hourly -> short candles, so a small pullback inside a bigger trend is visible.
  * Zones           = swing prices (plus prior day/week extremes and round numbers) that cluster inside a small band.
                      More touches, more time scales and more recent = stronger. Broken resistance becomes support (role reversal).
  * Trendlines      = through the last two swing lows (rising support) or swing highs (falling resistance).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

STEP_MIN = 5   # base candle size of the stored data

# label, timeframe (minutes or "D"), swing window (bars each side), weight in the alignment score
CRYPTO_TFS = (("Primary", "D", 3, 3.0), ("Secondary", 240, 3, 2.0), ("Intermediate", 60, 3, 1.5), ("Minor", 15, 4, 1.0), ("Micro", 5, 4, 0.5))
STOCK_TFS = (("Primary", "D", 3, 3.0), ("Secondary", 60, 3, 2.0), ("Intermediate", 15, 4, 1.5), ("Minor", 5, 4, 1.0))


# ------------------------------------------------------------------ basics

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def resample_tf(df5: pd.DataFrame, tf, offset_min: int = 0, session_end: str | None = None, keep_forming: bool = False) -> pd.DataFrame:
    """Complete candles of size `tf` (minutes, or "D") built from 5-minute candles. Index = candle start.
    offset_min anchors intraday candles (stocks: 30 so hours start at 09:30). session_end ("16:00") caps the last candle of a session."""
    if df5 is None or len(df5) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    rule = "1D" if tf == "D" else f"{int(tf)}min"
    kw = {} if tf == "D" else {"offset": f"{offset_min}min"}
    g = df5.resample(rule, closed="left", label="left", **kw)
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
    if len(out) == 0 or keep_forming:
        return out
    span = pd.Timedelta(days=1) if tf == "D" else pd.Timedelta(minutes=int(tf))
    start = out.index[-1]
    end = start + span
    if session_end:
        hh, mm = (int(x) for x in session_end.split(":"))
        cap = start.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        end = min(end, cap) if cap > start else end
    if df5.index[-1] + pd.Timedelta(minutes=STEP_MIN) < end:      # last candle still forming
        out = out.iloc[:-1]
    return out


def pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Confirmed swing highs/lows. Columns: kind ('H'/'L'), price, pos (the swing bar), cpos (bar at which it became known), time, ctime."""
    cols = ["kind", "price", "pos", "cpos", "time", "ctime"]
    n, w = len(df), left + right + 1
    if n < w:
        return pd.DataFrame(columns=cols)
    h, l = df.high.values.astype(float), df.low.values.astype(float)
    wh, wl = sliding_window_view(h, w), sliding_window_view(l, w)
    ch, cl = wh[:, left], wl[:, left]
    ph = (ch > wh[:, :left].max(axis=1)) & (ch >= wh[:, left + 1:].max(axis=1))
    pl = (cl < wl[:, :left].min(axis=1)) & (cl <= wl[:, left + 1:].min(axis=1))
    rows = []
    for k, flag, vals in (("H", ph, ch), ("L", pl, cl)):
        for j in np.flatnonzero(flag):
            pos = j + left
            rows.append((k, float(vals[j]), pos, pos + right))
    if not rows:
        return pd.DataFrame(columns=cols)
    p = pd.DataFrame(rows, columns=["kind", "price", "pos", "cpos"]).sort_values(["cpos", "kind"]).reset_index(drop=True)
    p["time"] = df.index[p.pos.values]
    p["ctime"] = df.index[p.cpos.values]
    return p


# ------------------------------------------------------------------ causal columns for strategies

def add_structure(d: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Adds sw_hi, sw_hi_prev, sw_lo, sw_lo_prev (the last two CONFIRMED swing prices) and sw_trend (+1 HH&HL, -1 LH&LL, 0 otherwise)."""
    p = pivots(d, left, right)
    n = len(d)
    for kind, name in (("H", "hi"), ("L", "lo")):
        arr = np.full(n, np.nan)
        pk = p[p.kind == kind] if len(p) else p
        if len(pk):
            arr[pk.cpos.values.astype(int)] = pk.price.values
        ser = pd.Series(arr, index=d.index)
        d[f"sw_{name}"] = ser.ffill()
        comp = ser.dropna()
        d[f"sw_{name}_prev"] = comp.shift(1).reindex(ser.index).ffill() if len(comp) else np.nan
    up = (d.sw_hi > d.sw_hi_prev) & (d.sw_lo > d.sw_lo_prev)
    dn = (d.sw_hi < d.sw_hi_prev) & (d.sw_lo < d.sw_lo_prev)
    d["sw_trend"] = np.where(up, 1, np.where(dn, -1, 0))
    return d


# ------------------------------------------------------------------ trend structure

def structure_from(hp, lp, close: float, atr_now: float, ema20: float | None = None, ema50: float | None = None) -> dict:
    """hp / lp: the last (up to two) confirmed swing-high / swing-low prices, oldest first."""
    out = {"direction": "RANGE", "pattern": "not enough swing points yet", "state": "unclear", "swing_high": None, "swing_low": None,
           "prev_swing_high": None, "prev_swing_low": None, "invalidation": None, "ema_state": "n/a"}
    if len(hp):
        out["swing_high"] = float(hp[-1])
    if len(lp):
        out["swing_low"] = float(lp[-1])
    if len(hp) > 1:
        out["prev_swing_high"] = float(hp[-2])
    if len(lp) > 1:
        out["prev_swing_low"] = float(lp[-2])
    if ema20 is not None and ema50 is not None and not (math.isnan(ema20) or math.isnan(ema50)):
        out["ema_state"] = ("bullish (price above rising averages)" if close > ema20 > ema50 else
                            "bearish (price below falling averages)" if close < ema20 < ema50 else "mixed")
    if len(hp) < 2 or len(lp) < 2:
        return out
    hh, hl = hp[-1] > hp[-2], lp[-1] > lp[-2]
    if hh and hl:
        out.update(direction="UP", pattern="higher highs and higher lows", state="intact", invalidation=out["swing_low"])
        if close < out["swing_low"]:
            out["state"] = "broken"
    elif (not hh) and (not hl):
        out.update(direction="DOWN", pattern="lower highs and lower lows", state="intact", invalidation=out["swing_high"])
        if close > out["swing_high"]:
            out["state"] = "broken"
    elif (not hh) and hl:
        out.update(direction="RANGE", pattern="lower highs but higher lows (price is coiling, a breakout is coming)", state="coiling")
    else:
        out.update(direction="RANGE", pattern="higher highs but lower lows (widening, choppy)", state="choppy")
    return out


def structure(piv: pd.DataFrame, close: float, atr_now: float, ema20: float | None = None, ema50: float | None = None) -> dict:
    H = piv[piv.kind == "H"].price.tail(2).tolist() if len(piv) else []
    L = piv[piv.kind == "L"].price.tail(2).tolist() if len(piv) else []
    return structure_from(H, L, close, atr_now, ema20, ema50)


def alignment(tfs: list[dict]) -> dict:
    """Combine the scales. Score runs from -1 (all down) to +1 (all up)."""
    wsum = sum(t["weight"] for t in tfs) or 1.0
    sgn = lambda t: 0 if t["state"] == "broken" else {"UP": 1, "DOWN": -1}.get(t["direction"], 0)
    score = sum(t["weight"] * sgn(t) for t in tfs) / wsum
    big = [t for t in tfs if t["label"] in ("Primary", "Secondary")]
    big_s = (sum(t["weight"] * sgn(t) for t in big) / (sum(t["weight"] for t in big) or 1.0)) if big else 0.0
    small = [t for t in tfs if t["label"] in ("Intermediate", "Minor", "Micro")]
    small_s = (sum(t["weight"] * sgn(t) for t in small) / (sum(t["weight"] for t in small) or 1.0)) if small else 0.0
    if score >= 0.75:
        text, kind = "All time scales point UP. The trend is aligned.", "ALIGNED_UP"
    elif score <= -0.75:
        text, kind = "All time scales point DOWN. The trend is aligned.", "ALIGNED_DOWN"
    elif big_s > 0.3 and small_s <= 0:
        text, kind = "A pullback inside a larger UPTREND. This is the classic 'buy the dip' area, but wait for the small trend to turn back up.", "PULLBACK_IN_UPTREND"
    elif big_s < -0.3 and small_s >= 0:
        text, kind = "A bounce inside a larger DOWNTREND. Moves against the bigger trend are lower odds.", "BOUNCE_IN_DOWNTREND"
    elif big_s > 0.3:
        text, kind = "The larger trend is UP and the smaller scales are mostly with it.", "MOSTLY_UP"
    elif big_s < -0.3:
        text, kind = "The larger trend is DOWN and the smaller scales are mostly with it.", "MOSTLY_DOWN"
    else:
        text, kind = "No clear trend across the scales (range or transition). Levels matter more than trend here.", "MIXED"
    bias = "UP" if score > 0.3 else "DOWN" if score < -0.3 else "NEUTRAL"
    return {"score": round(float(score), 2), "kind": kind, "text": text, "bias": bias}


# ------------------------------------------------------------------ zones

def _round_step(price: float) -> float:
    return 10 ** (math.floor(math.log10(max(price, 1e-9))) - 1)


def _cluster(points: list[dict], tol: float) -> list[list[dict]]:
    pts = sorted(points, key=lambda x: x["price"])
    groups: list[list[dict]] = []
    for p in pts:
        if groups and p["price"] - groups[-1][0]["price"] <= 2 * tol and p["price"] - groups[-1][-1]["price"] <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def build_zones(points: list[dict], price: float, tol: float, atr_ref: float, now: pd.Timestamp, max_each: int = 4) -> list[dict]:
    zones = []
    for g in _cluster(points, tol):
        prices = [p["price"] for p in g]
        lo, hi = min(prices), max(prices)
        pad = 0.15 * tol
        mid = float(np.mean(prices))
        score = sum(p["w"] for p in g)
        times = [p["time"] for p in g if p.get("time") is not None]
        last = max(times) if times else None
        if last is not None:
            age_days = max((now - last).total_seconds() / 86400, 0)
            score *= 1.0 if age_days <= 30 else 0.85 if age_days <= 90 else 0.7
        else:
            age_days = None
        kinds = {p["kind"] for p in g}
        tfs = sorted({p["tf"] for p in g if p.get("tf")})
        labels = sorted({p["label"] for p in g if p.get("label")})
        inside = (lo - pad) <= price <= (hi + pad)
        role = "inside" if inside else ("support" if mid < price else "resistance")
        former = None
        solid = sum(1 for p in g if p["kind"] in ("H", "L")) >= 2
        if solid and role == "support" and "H" in kinds and "L" not in kinds:
            former = "was resistance, now support (role reversal)"
        if solid and role == "resistance" and "L" in kinds and "H" not in kinds:
            former = "was support, now resistance (role reversal)"
        zones.append({"low": float(lo - pad), "high": float(hi + pad), "mid": mid, "role": role, "former": former,
                      "touches": int(sum(1 for p in g if p["kind"] in ("H", "L"))), "tfs": tfs, "labels": labels,
                      "score": round(float(score), 2), "strength": "Strong" if score >= 6 else "Moderate" if score >= 3 else "Weak",
                      "dist_atr": round(abs(mid - price) / atr_ref, 2) if atr_ref else None,
                      "dist_pct": round(100 * abs(mid - price) / price, 2),
                      "age_days": None if age_days is None else round(age_days, 1), "last_touch": None if last is None else str(last)})
    sup = sorted([z for z in zones if z["role"] == "support"], key=lambda z: -z["mid"])[:max_each]
    res = sorted([z for z in zones if z["role"] == "resistance"], key=lambda z: z["mid"])[:max_each]
    ins = [z for z in zones if z["role"] == "inside"]
    return sorted(sup + ins + res, key=lambda z: -z["mid"])


def trendline(piv: pd.DataFrame, kind: str, df: pd.DataFrame, atr_now: float, label: str) -> dict | None:
    """Rising support line through the last two swing lows, or falling resistance line through the last two swing highs."""
    pk = piv[piv.kind == kind].tail(4) if len(piv) else piv
    if len(pk) < 2:
        return None
    a, b = pk.iloc[-2], pk.iloc[-1]
    if len(df) - 1 - b.pos > 60 or b.pos - a.pos > 80:      # stale or stretched lines are not useful
        return None
    rising_lows, falling_highs = kind == "L" and b.price > a.price, kind == "H" and b.price < a.price
    if not (rising_lows or falling_highs):
        return None
    slope = (b.price - a.price) / (b.pos - a.pos)
    now_pos = len(df) - 1
    line_now = b.price + slope * (now_pos - b.pos)
    touches = 2
    for _, q in (piv[(piv.kind == kind) & (piv.pos > b.pos)].iterrows() if len(piv) else []):
        if abs(q.price - (b.price + slope * (q.pos - b.pos))) <= 0.35 * atr_now:
            touches += 1
    close = float(df.close.iloc[-1])
    broken = (close < line_now - 0.25 * atr_now) if kind == "L" else (close > line_now + 0.25 * atr_now)
    return {"tf": label, "kind": "support" if kind == "L" else "resistance", "t1": str(a.time), "p1": float(a.price), "t2": str(b.time), "p2": float(b.price),
            "t_now": str(df.index[-1]), "p_now": float(line_now), "touches": touches, "broken": bool(broken),
            "note": "rising support line" if kind == "L" else "falling resistance line"}


# ------------------------------------------------------------------ full analysis

def _avail(index: pd.DatetimeIndex, tf, session_end: str | None) -> np.ndarray:
    """When each candle is COMPLETE (candle start + length, capped at the session close for stocks)."""
    span = pd.Timedelta(days=1) if tf == "D" else pd.Timedelta(minutes=int(tf))
    end = index + span
    if session_end:
        hh, mm = (int(x) for x in session_end.split(":"))
        cap = index.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        end = pd.DatetimeIndex(np.where((cap > index) & (cap < end), cap, end), tz=index.tz) if len(index) else end
    return end.values


class Engine:
    """Precomputes candles, swing points and averages once, then answers "what did the chart look like at time t?" using only
    candles that were complete at t. Used for live analysis (t = now) and for causal back-tests (t = each signal)."""

    def __init__(self, df5: pd.DataFrame, tfs=CRYPTO_TFS, offset_min: int = 0, session_end: str | None = None):
        self.df5, self.tfs, self.session_end = df5, tfs, session_end
        self.frames = {}
        for label, tf, lr, weight in tfs:
            f = resample_tf(df5, tf, offset_min, session_end, keep_forming=True)
            name = "1D" if tf == "D" else (f"{int(tf) // 60}h" if int(tf) >= 60 else f"{int(tf)}m")
            if len(f) == 0:
                self.frames[label] = None
                continue
            av = _avail(f.index, tf, session_end)
            p = pivots(f, lr, lr)
            p_av = av[p.cpos.values.astype(int)] if len(p) else np.array([], dtype="datetime64[ns]")
            hmask = (p.kind == "H").values if len(p) else np.array([], dtype=bool)
            self.frames[label] = {"f": f, "av": av, "atr": _atr(f).values, "e20": _ema(f.close, 20).values, "e50": _ema(f.close, 50).values, "p": p, "p_av": p_av,
                                  "name": name, "weight": weight, "lr": lr,
                                  "pp": p.price.values if len(p) else np.array([]), "pk": p.kind.values if len(p) else np.array([]),
                                  "pt": list(p.time) if len(p) else [], "hp": p.price.values[hmask] if len(p) else np.array([]),
                                  "hav": p_av[hmask] if len(p) else p_av, "lp": p.price.values[~hmask] if len(p) else np.array([]),
                                  "lav": p_av[~hmask] if len(p) else p_av}
        d = resample_tf(df5, "D", 0, session_end, keep_forming=True)
        self.daily = d
        self.d_av = _avail(d.index, "D", session_end) if len(d) else np.array([], dtype="datetime64[ns]")

    def at(self, t: pd.Timestamp, full: bool = True, max_pivots_shown: int = 12) -> dict:
        """Chart analysis using candles complete at time t (t = the close time of the newest candle you may use)."""
        out = {"ok": False, "asof": str(t), "price": None, "timeframes": [], "alignment": None, "zones": [], "nearest_support": None,
               "nearest_resistance": None, "trendlines": [], "pivots": {}, "reading": [], "notes": []}
        tt = np.datetime64(t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t, "ns")
        # tz-aware indexes store UTC values in .values, so compare in UTC
        n5 = int(self.df5.index.searchsorted(t - pd.Timedelta(minutes=STEP_MIN), side="right"))
        if n5 < 200:
            out["notes"].append("Not enough candles yet.")
            return out
        price = float(self.df5.close.iloc[n5 - 1])
        out["price"] = price
        points, rows, lines = [], [], []
        atr_ref = None
        for label, tf, lr, weight in self.tfs:
            fr = self.frames.get(label)
            name = "1D" if tf == "D" else (f"{int(tf) // 60}h" if int(tf) >= 60 else f"{int(tf)}m")
            k = int(np.searchsorted(fr["av"], tt, side="right")) if fr else 0
            if fr is None or k < 2 * lr + 3:
                rows.append({"label": label, "tf": name, "weight": weight, "direction": "RANGE", "state": "unclear", "pattern": "not enough history on this time scale yet",
                             "bars": int(k), "limited_history": True, "swing_high": None, "swing_low": None, "invalidation": None, "ema_state": "n/a"})
                continue
            m = int(np.searchsorted(fr["p_av"], tt, side="right"))
            mh, ml = int(np.searchsorted(fr["hav"], tt, side="right")), int(np.searchsorted(fr["lav"], tt, side="right"))
            a_now = float(fr["atr"][k - 1]) if fr["atr"][k - 1] == fr["atr"][k - 1] else 0.0
            e20, e50 = fr["e20"][k - 1], fr["e50"][k - 1]
            st = structure_from(fr["hp"][max(0, mh - 2):mh], fr["lp"][max(0, ml - 2):ml], float(fr["f"].close.values[k - 1]), a_now,
                                None if e20 != e20 else float(e20), None if e50 != e50 else float(e50))
            rows.append({"label": label, "tf": name, "weight": weight, "bars": k, "limited_history": bool(k < 40), **st})
            if a_now and (label == "Intermediate" or atr_ref is None and label in ("Secondary", "Minor")):
                atr_ref = a_now
            lim = 12 * (a_now or price * 0.01)
            for i in (range(max(0, m - 14), m) if label != "Micro" else ()):   # the 5-minute scale reads the trend only; its swings are too noisy to be zones
                if abs(fr["pp"][i] - price) <= lim:
                    points.append({"price": float(fr["pp"][i]), "kind": str(fr["pk"][i]), "w": weight, "time": fr["pt"][i], "tf": name, "label": label})
            if full:
                p = fr["p"].iloc[:m]
                fk = fr["f"].iloc[:k]
                for kind in ("L", "H"):
                    tl = trendline(p, kind, fk, a_now, label)
                    if tl:
                        lines.append({**tl, "tf": name, "scale": label})
                out["pivots"][label] = [{"time": str(q.time), "price": float(q.price), "kind": q.kind} for q in p.tail(max_pivots_shown).itertuples()]
        out["timeframes"] = rows
        out["alignment"] = alignment(rows)
        atr_ref = atr_ref or price * 0.005
        # classic anchors from COMPLETE days only: yesterday's extremes, the last 7 days' extremes, round numbers
        kd = int(np.searchsorted(self.d_av, tt, side="right")) if len(self.d_av) else 0
        now = t
        if kd >= 1:
            y = self.daily.iloc[kd - 1]
            for nm, v in (("prior day high", y.high), ("prior day low", y.low), ("prior day close", y.close)):
                points.append({"price": float(v), "kind": "A", "w": 1.0, "time": self.daily.index[kd - 1], "tf": "1D", "label": nm})
        if kd >= 7:
            wk = self.daily.iloc[kd - 7:kd]
            points.append({"price": float(wk.high.max()), "kind": "A", "w": 1.5, "time": wk.high.idxmax(), "tf": "1W", "label": "prior 7-day high"})
            points.append({"price": float(wk.low.min()), "kind": "A", "w": 1.5, "time": wk.low.idxmin(), "tf": "1W", "label": "prior 7-day low"})
        step = _round_step(price)
        lo_r, hi_r = price - 6 * atr_ref, price + 6 * atr_ref
        for k in range(int(lo_r // step), int(hi_r // step) + 2):
            v = k * step
            if lo_r <= v <= hi_r:
                major = (k % 5 == 0)
                points.append({"price": float(v), "kind": "R", "w": 1.0 if major else 0.4, "time": None, "tf": None, "label": "major round number" if major else "round number"})
        zones = build_zones(points, price, 0.4 * atr_ref, atr_ref, now)
        out["zones"] = zones
        sup = [z for z in zones if z["role"] == "support"]
        res = [z for z in zones if z["role"] == "resistance"]
        out["nearest_support"] = max(sup, key=lambda z: z["mid"]) if sup else None
        out["nearest_resistance"] = min(res, key=lambda z: z["mid"]) if res else None
        out["trendlines"] = lines
        out["atr_ref"] = round(atr_ref, 6)
        out["reading"] = _reading(out)
        out["ok"] = True
        return out


def analyze(df5: pd.DataFrame, tfs=CRYPTO_TFS, offset_min: int = 0, session_end: str | None = None, max_pivots_shown: int = 12) -> dict:
    """df5: COMPLETED 5-minute candles. Returns a JSON-friendly description of the chart as of the newest candle."""
    if df5 is None or len(df5) < 200:
        return {"ok": False, "asof": None, "price": None, "timeframes": [], "alignment": None, "zones": [], "nearest_support": None,
                "nearest_resistance": None, "trendlines": [], "pivots": {}, "reading": [], "notes": ["Not enough candles yet."]}
    eng = Engine(df5, tfs, offset_min, session_end)
    return eng.at(df5.index[-1] + pd.Timedelta(minutes=STEP_MIN), True, max_pivots_shown)


def _n(k: int, word: str) -> str:
    return f"{k} {word}" + ("" if k == 1 else ("es" if word.endswith("ch") else "s"))


def _reading(a: dict) -> list[str]:
    """Plain-English summary a chart technician would give."""
    r = [a["alignment"]["text"]]
    s, rs = a.get("nearest_support"), a.get("nearest_resistance")
    price = a["price"]
    inside = [z for z in a["zones"] if z["role"] == "inside"]
    if inside:
        z = inside[0]
        r.append(f"Price is sitting inside a {z['strength'].lower()} zone ({z['low']:.6g} to {z['high']:.6g}); expect a reaction here (a bounce, or a break that then flips the zone).")
    if s:
        r.append(f"Nearest support: {s['mid']:.6g} ({s['strength'].lower()}, {_n(s['touches'], 'touch')}, {s['dist_atr']} ATR away)" + (f"; {s['former']}" if s["former"] else "") + ".")
    if rs:
        r.append(f"Nearest resistance: {rs['mid']:.6g} ({rs['strength'].lower()}, {_n(rs['touches'], 'touch')}, {rs['dist_atr']} ATR away)" + (f"; {rs['former']}" if rs["former"] else "") + ".")
    broken = [t for t in a["timeframes"] if t["state"] == "broken"]
    for t in broken:
        r.append(f"On the {t['tf']} chart the {t['direction'].lower()}trend structure just broke (price closed beyond {t['invalidation']:.6g}). Treat that scale as unreliable until a new swing forms.")
    coil = [t for t in a["timeframes"] if t["state"] == "coiling"]
    for t in coil:
        r.append(f"The {t['tf']} chart is coiling (lower highs, higher lows); a break of either side usually starts a move.")
    bl = [l for l in a["trendlines"] if l["broken"]]
    for l in bl[:2]:
        r.append(f"Price has broken the {l['tf']} {l['note']} (it was touched {l['touches']} times).")
    return r


# ------------------------------------------------------------------ room to run

def room_to_levels(a: dict | None, entry: float, risk: float, t1: float, t2: float, long: bool = True) -> dict:
    """How much space is there before the next opposing zone? Distances are in R (multiples of the planned risk)."""
    if not a or not a.get("ok") or risk <= 0:
        return {"known": False, "text": ""}
    zs = a["zones"]
    if long:
        opp = [z for z in zs if z["low"] > entry]
        z = min(opp, key=lambda z: z["low"]) if opp else None
        dist = (z["low"] - entry) / risk if z else None
        sup = [z2 for z2 in zs if z2["high"] < entry]
        s = max(sup, key=lambda z2: z2["high"]) if sup else None
    else:
        opp = [z for z in zs if z["high"] < entry]
        z = max(opp, key=lambda z: z["high"]) if opp else None
        dist = (entry - z["high"]) / risk if z else None
        sup = [z2 for z2 in zs if z2["low"] > entry]
        s = min(sup, key=lambda z2: z2["low"]) if sup else None
    t1_r, t2_r = abs(t1 - entry) / risk, abs(t2 - entry) / risk
    blocks_t1 = bool(z and dist is not None and dist < t1_r and z["strength"] != "Weak")
    blocks_t2 = bool(z and dist is not None and dist < t2_r and z["strength"] != "Weak")
    word = "resistance" if long else "support"
    if z is None:
        text = f"No known {word} zone between entry and the targets."
    else:
        text = f"Next {word} zone: {z['mid']:.6g} ({z['strength'].lower()}), {dist:.1f}R from entry"
        text += " - BEFORE target 1." if blocks_t1 else " - before target 2." if blocks_t2 else " - beyond both targets."
    return {"known": True, "opposing": None if z is None else {k: z[k] for k in ("low", "high", "mid", "strength", "touches")},
            "dist_r": None if dist is None else round(float(dist), 2), "blocks_t1": blocks_t1, "blocks_t2": blocks_t2, "text": text,
            "backing": None if s is None else {"mid": s["mid"], "strength": s["strength"]}}


# ------------------------------------------------------------------ chart-based exit plan

STRUCTURE_DEFAULTS = {"min_room_r": 1.5, "min_risk_atr": 0.8, "max_risk_atr": 3.0, "buffer_atr": 0.25}


def structure_plan(a: dict | None, entry: float, atr: float, long: bool = True, min_room_r: float = 1.5, min_risk_atr: float = 0.8,
                   max_risk_atr: float = 3.0, buffer_atr: float = 0.25, strengths=("Moderate", "Strong")) -> dict:
    """Stop and targets taken from the chart instead of fixed ATR multiples (works for longs, and for shorts when long=False).

    Rules (fixed in advance, nothing tuned):
      * Stop  = just beyond the nearest backing zone (support for a long). A zone closer than `min_risk_atr` ATR is skipped as too tight;
                if the next backing zone is farther than `max_risk_atr` ATR there is no sensible stop and the trade is passed.
      * Target 1 = just before the nearest opposing zone (resistance for a long). It must be at least `min_room_r` times the risk away,
                otherwise there is no room and the trade is passed.
      * Target 2 = just before the next opposing zone, or 1.5R beyond target 1 when the sky is clear.
      * With no opposing zone at all: target 1 = 1.5R, target 2 = 3R (the usual rule).
    """
    fail = lambda why: {"ok": False, "fail": why, "text": f"No chart-based plan: {why}."}
    if not a or not a.get("ok") or not atr or atr <= 0:
        return fail("the chart analysis is not available yet")
    zs = [z for z in a["zones"] if z["strength"] in strengths and z["role"] != "inside"]
    sgn = 1 if long else -1
    back = sorted([z for z in zs if (z["high"] < entry if long else z["low"] > entry)], key=lambda z: -z["high"] if long else z["low"])
    opp = sorted([z for z in zs if (z["low"] > entry if long else z["high"] < entry)], key=lambda z: z["low"] if long else -z["high"])
    stop, stop_zone = None, None
    for z in back:
        s_px = (z["low"] - buffer_atr * atr) if long else (z["high"] + buffer_atr * atr)
        if sgn * (entry - s_px) >= min_risk_atr * atr:
            stop, stop_zone = s_px, z
            break
    if stop is None:
        return fail("there is no support zone to hide the stop behind" if long else "there is no resistance zone to hide the stop behind")
    risk = sgn * (entry - stop)
    if risk > max_risk_atr * atr:
        return fail(f"the nearest {'support' if long else 'resistance'} is too far away ({risk / atr:.1f} ATR), so the stop would be too wide")
    gap = 0.1 * atr
    t1_zone = opp[0] if opp else None
    if t1_zone is not None:
        t1 = (t1_zone["low"] - gap) if long else (t1_zone["high"] + gap)
        room = sgn * (t1 - entry) / risk
        if room < min_room_r:
            return fail(f"the nearest {'resistance' if long else 'support'} is only {max(room, 0):.1f}R away (needs {min_room_r:g}R of room)")
        t1_basis = f"just {'under' if long else 'over'} a {t1_zone['strength'].lower()} {'resistance' if long else 'support'} zone"
        nxt = next((z for z in opp[1:] if sgn * ((z["low"] if long else z["high"]) - t1) >= 0.5 * risk), None)
        if nxt is not None:
            t2 = (nxt["low"] - gap) if long else (nxt["high"] + gap)
            t2_basis = f"just {'under' if long else 'over'} the next {nxt['strength'].lower()} zone"
        else:
            t2, t2_basis = t1 + sgn * 1.5 * risk, "1.5R past target 1, no further zone"
    else:
        t1, t2 = entry + sgn * 1.5 * risk, entry + sgn * 3.0 * risk
        t1_basis, t2_basis = "no zone in the way", "no zone in the way"
    t1_r, t2_r = sgn * (t1 - entry) / risk, sgn * (t2 - entry) / risk
    stop_basis = f"just {'below' if long else 'above'} a {stop_zone['strength'].lower()} {'support' if long else 'resistance'} zone"
    text = (f"Chart-based plan: stop {stop:.6g} ({stop_basis}), target 1 {t1:.6g} ({t1_basis}, {t1_r:.1f}R), "
            f"target 2 {t2:.6g} ({t2_basis}, {t2_r:.1f}R).")
    return {"ok": True, "fail": "", "stop": float(stop), "t1": float(t1), "t2": float(t2), "risk": float(risk), "t1_r": float(t1_r), "t2_r": float(t2_r),
            "blended_rr": float(0.5 * t1_r + 0.5 * t2_r), "stop_basis": stop_basis, "t1_basis": t1_basis, "t2_basis": t2_basis, "text": text}


def confluence_check(a: dict | None, entry: float, atr: float, bar_low: float, bar_close: float, bar_high: float | None = None,
                     long: bool = True, touch_atr: float = 0.25, max_dist_atr: float = 1.5) -> dict:
    """The 'trader-style' checklist for trading a rejection at a level (long: buy at support; short: sell at resistance).
    Every rule is fixed in advance (nothing tuned). ALL must hold:
      1. Bigger picture: the combined trend across the scales agrees (aligned, a pullback inside the bigger trend, or mostly with it).
      2. Location: the signal candle reached a STRONG zone (within `touch_atr` ATR of it) and closed back away from it.
      3. Not chasing: the entry is no more than `max_dist_atr` ATR away from that zone.
    Room before the next opposing zone and the stop behind the zone are checked by structure_plan with its own rules."""
    no = lambda why: {"ok": False, "why": why, "zone": None}
    if not a or not a.get("ok") or not atr or atr <= 0:
        return no("the chart analysis is not available yet")
    al = a.get("alignment") or {}
    good = ("ALIGNED_UP", "PULLBACK_IN_UPTREND", "MOSTLY_UP") if long else ("ALIGNED_DOWN", "BOUNCE_IN_DOWNTREND", "MOSTLY_DOWN")
    if al.get("kind") not in good:
        return no(f"the bigger trend is not {'up' if long else 'down'} (" + str(al.get("kind", "unknown")).replace("_", " ").lower() + ")")
    if long:
        strong = [z for z in a["zones"] if z["strength"] == "Strong" and z["role"] in ("support", "inside")]
        touched = [z for z in strong if bar_low <= z["high"] + touch_atr * atr and bar_low >= z["low"] - 1.5 * atr and bar_close > z["high"]]
        if not touched:
            return no("the candle did not reject a strong support zone")
        z = max(touched, key=lambda q: q["high"])
        if entry - z["high"] > max_dist_atr * atr:
            return no(f"price is already {((entry - z['high']) / atr):.1f} ATR above that support (too late)")
    else:
        hi = bar_high if bar_high is not None else bar_close
        strong = [z for z in a["zones"] if z["strength"] == "Strong" and z["role"] in ("resistance", "inside")]
        touched = [z for z in strong if hi >= z["low"] - touch_atr * atr and hi <= z["high"] + 1.5 * atr and bar_close < z["low"]]
        if not touched:
            return no("the candle did not reject a strong resistance zone")
        z = min(touched, key=lambda q: q["low"])
        if z["low"] - entry > max_dist_atr * atr:
            return no(f"price is already {((z['low'] - entry) / atr):.1f} ATR below that resistance (too late)")
    return {"ok": True, "why": "", "zone": {k: z[k] for k in ("low", "high", "mid", "strength", "touches")}}


def trend_break_warning(a: dict | None, long: bool = True, scales=("Intermediate", "Secondary")) -> str:
    """Advisory text when the trend structure behind a trade has broken on the scales that matter for it. Empty when fine."""
    if not a or not a.get("ok"):
        return ""
    want = "UP" if long else "DOWN"
    for t in a["timeframes"]:
        if t["label"] in scales and t["direction"] == want and t["state"] == "broken" and t.get("invalidation") is not None:
            side = "below" if long else "above"
            return (f"The {t['tf']} {'up' if long else 'down'}trend structure just broke (price closed {side} {t['invalidation']:.6g}). "
                    "The reason for this trade is weakening; consider getting out.")
    return ""
