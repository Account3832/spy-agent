"""Free-data provider registry, $0 cost guard, retry/circuit breaker, and adapters.

The registry records what each provider was found to offer when verified (2026-10-12),
and how strong the evidence was. Nothing here upgrades a plan or starts a trial.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict

import pandas as pd

from .config import Settings, TZ

NO_OPTIONS_MSG = "REAL-TIME SPY OPTIONS QUOTES UNAVAILABLE UNDER THE CURRENT $0 DATA REQUIREMENT."


class CostViolation(Exception):
    pass


class DataError(Exception):
    pass


class CircuitOpen(Exception):
    pass


@dataclass(frozen=True)
class Capability:
    name: str
    kind: str                    # underlying | options | both
    monthly_cost_usd: float
    requires_paid_subscription: bool
    realtime: str                # realtime_licensed | indicative | delayed | unofficial_unverified | none
    spy_underlying: str          # coverage of SPY shares/ETF prices
    spy_options: str             # coverage of SPY option quotes
    bid_ask: str
    history: str
    auth: str
    automated_use: str           # allowed | prohibited | unverified
    enabled: bool                # enabled by default in this project
    evidence: str                # official docs | third-party summary | not verified this session
    notes: str = ""
    verified_on: str = "2026-10-12"


REGISTRY: list[Capability] = [
    Capability("alpaca_equities_iex", "underlying", 0.0, False, "realtime_licensed",
               "yes (SPY, IEX exchange only)", "no", "IEX-only quotes",
               "history depth not verified; adapter reports what it gets",
               "free API key + account (paper account is enough)", "allowed", True, "official docs",
               "Free plan real-time covers only the IEX exchange, not the consolidated market. Prices track SPY "
               "closely, but volume is a small fraction of total volume (relative volume/VWAP still usable)."),
    Capability("yahoo_finance_unofficial", "underlying", 0.0, False, "unofficial_unverified",
               "yes (SPY)", "no", "none", "5m ~60 days, 1h ~730 days, daily many years (yfinance behaviour)",
               "none", "unverified", True, "not verified this session",
               "Unofficial endpoint via yfinance. No SLA, may rate-limit or break. Used only as the FALLBACK and "
               "labelled DELAYED/unverified, never as licensed live data."),
    Capability("alpaca_options_indicative", "options", 0.0, False, "indicative",
               "no", "likely yes for SPY (unverified), indicative only", "indicative (not actual OPRA quotes)",
               "options history since Feb 2024; free trades delayed 15 min", "free API key + account",
               "allowed", False, "official docs",
               "Free feed is a derivative of OPRA, explicitly NOT actual OPRA quotes. The OPRA feed is paid. "
               "Disabled by default; if enabled its quotes are research-only and can never be treated as executable."),
    Capability("opra_via_alpaca", "options", 99.0, True, "realtime_licensed", "no", "yes", "yes",
               "since Feb 2024", "paid plan", "allowed", False, "official docs",
               "Alpaca's paid plan lists $99/month for full OPRA. Excluded by the $0 budget."),
    Capability("intrinio_options_15min", "options", 800.0, True, "delayed", "no", "yes", "yes (delayed)",
               "n/a", "API key + paid plan", "allowed", False, "official docs",
               "Listed at $9,600/yr (about $800/month). Excluded by the $0 budget. Do not start its free trial."),
    Capability("interactive_brokers_api", "both", 0.0, True, "realtime_licensed", "yes", "yes", "yes",
               "limited", "funded brokerage account", "allowed", False, "not verified this session",
               "Real-time options data needs a funded account and paid market-data entitlements "
               "(general knowledge, not re-verified). Excluded by the $0 budget."),
    Capability("cboe_delayed_web_pages", "both", 0.0, False, "delayed", "n/a (Cboe focus)", "n/a (Cboe focus)", "delayed",
               "none", "none", "prohibited", False, "third-party summary",
               "A third-party guide reports automated extraction from Cboe delayed-quote pages is prohibited. "
               "NOT integrated; irrelevant now that the project trades SPY."),
]


def get_capability(name: str) -> Capability:
    for c in REGISTRY:
        if c.name == name:
            return c
    raise KeyError(name)


def cost_violations(settings: Settings, registry: list[Capability] | None = None) -> list[str]:
    """Any ENABLED provider that costs money or needs a paid subscription breaks the $0 budget."""
    out = []
    for c in registry or REGISTRY:
        if not c.enabled:
            continue
        if c.monthly_cost_usd > settings.data_budget_usd or (c.requires_paid_subscription and not settings.allow_paid_providers):
            out.append(f"{c.name}: cost ${c.monthly_cost_usd}/mo, paid subscription={c.requires_paid_subscription}")
    return out


def assert_free(name: str, settings: Settings) -> Capability:
    c = get_capability(name)
    if c.monthly_cost_usd > settings.data_budget_usd or (c.requires_paid_subscription and not settings.allow_paid_providers):
        raise CostViolation(f"{name} is not free (${c.monthly_cost_usd}/mo, paid={c.requires_paid_subscription})")
    if c.automated_use == "prohibited":
        raise CostViolation(f"{name} prohibits automated access")
    return c


def capability_report_md() -> str:
    lines = ["| Provider | Cost/mo | Real-time class | SPY prices | SPY options | Bid/ask | Automated use | Enabled | Evidence |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in REGISTRY:
        lines.append(f"| {c.name} | ${c.monthly_cost_usd:g} | {c.realtime} | {c.spy_underlying} | {c.spy_options} | "
                     f"{c.bid_ask} | {c.automated_use} | {'yes' if c.enabled else 'no'} | {c.evidence} |")
    return "\n".join(lines)


def data_status(cap: Capability, last_bar_end: pd.Timestamp | None, now: pd.Timestamp,
                historical: bool = False, simulated: bool = False, max_age_sec: int = 720) -> str:
    """live | delayed | historical | simulated. 'live' needs a licensed real-time source AND a fresh bar."""
    if simulated:
        return "simulated"
    if historical or last_bar_end is None:
        return "historical"
    age = (now - last_bar_end).total_seconds()
    if cap.realtime == "realtime_licensed" and age <= max_age_sec:
        return "live"
    return "delayed" if age <= max_age_sec else "historical"


# ---------------------------------------------------------------- resilience

class CircuitBreaker:
    def __init__(self, threshold: int = 3, reset_after: float = 300.0, clock=time.monotonic):
        self.threshold, self.reset_after, self.clock = threshold, reset_after, clock
        self.failures, self.opened_at = 0, None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.reset_after:  # half-open: allow one probe
            return True
        return False

    def success(self):
        self.failures, self.opened_at = 0, None

    def failure(self):
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()

    @property
    def state(self) -> str:
        return "open" if self.opened_at is not None and not self.allow() else ("half-open" if self.opened_at is not None else "closed")


def with_retry(fn, tries: int = 3, base: float = 1.0, max_delay: float = 20.0,
               breaker: CircuitBreaker | None = None, sleep=time.sleep):
    """Exponential backoff, bounded. Honours the circuit breaker so a dead provider is not hammered."""
    last = None
    for attempt in range(tries):
        if breaker is not None and not breaker.allow():
            raise CircuitOpen("circuit open; provider temporarily disabled")
        try:
            res = fn()
            if breaker is not None:
                breaker.success()
            return res
        except CircuitOpen:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if breaker is not None:
                breaker.failure()
            if attempt < tries - 1:
                sleep(min(max_delay, base * (2 ** attempt)))
    raise DataError(f"failed after {tries} attempts: {last}")


# ------------------------------------------------------------------- Yahoo

def normalize_intraday(raw: pd.DataFrame, now: pd.Timestamp | None = None, interval_min: int = 5) -> pd.DataFrame:
    """Pure function: raw yfinance frame -> clean NY-tz OHLCV, regular session only, completed bars only."""
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    need = ["open", "high", "low", "close", "volume"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise DataError(f"missing columns {miss}")
    df = df[need]
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    df.index = idx.tz_convert(TZ)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0.0)
    df = df.between_time("09:30", "15:59")
    if now is not None:  # a bar is usable only after its closing timestamp
        df = df[df.index + pd.Timedelta(minutes=interval_min) <= now]
    return df.astype(float)


def normalize_daily(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    idx = idx.tz_localize(None) if idx.tz is not None else idx
    df.index = idx.normalize().tz_localize(TZ)
    df = df[~df.index.duplicated(keep="last")].sort_index().dropna(subset=["close"])
    return df.astype(float)


class YahooProvider:
    """Unofficial Yahoo access through yfinance (imported lazily). Label its output 'delayed'."""
    capability = "yahoo_finance_unofficial"

    def __init__(self, breaker: CircuitBreaker | None = None):
        self.breaker = breaker or CircuitBreaker()

    def _download(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        import yfinance as yf

        def go():
            df = yf.download(symbol, interval=interval, period=period, progress=False,
                             auto_adjust=False, threads=False)
            if df is None or len(df) == 0:
                raise DataError(f"empty response for {symbol} {interval} {period}")
            return df
        return with_retry(go, breaker=self.breaker)

    def intraday(self, symbol: str, interval: str = "5m", period: str = "60d", now: pd.Timestamp | None = None):
        mins = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60}[interval]
        return normalize_intraday(self._download(symbol, interval, period), now or pd.Timestamp.now(tz=TZ), mins)

    def daily(self, symbol: str, period: str = "5y"):
        return normalize_daily(self._download(symbol, "1d", period))


# ------------------------------------------------------ Alpaca (IEX bars)

def parse_alpaca_bars(rows: list[dict], now: pd.Timestamp | None = None, interval_min: int = 5) -> pd.DataFrame:
    """Alpaca bar timestamps are bar START times in UTC, same convention as this project."""
    if not rows:
        raise DataError("no bars returned")
    df = pd.DataFrame(rows).rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    return normalize_intraday(df, now, interval_min)


class AlpacaProvider:
    """Free Alpaca market data, IEX feed only. Needs ALPACA_KEY_ID and ALPACA_SECRET_KEY.
    UNTESTED against the live API (no network in the build environment)."""
    capability = "alpaca_equities_iex"

    def __init__(self, key_id: str | None, secret: str | None, settings: Settings, breaker: CircuitBreaker | None = None):
        assert_free(self.capability, settings)
        if not key_id or not secret:
            raise DataError("ALPACA_KEY_ID / ALPACA_SECRET_KEY not set")
        self.h = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.breaker = breaker or CircuitBreaker()

    def intraday(self, symbol: str = "SPY", days: int = 30, now: pd.Timestamp | None = None) -> pd.DataFrame:
        import requests
        now = now or pd.Timestamp.now(tz=TZ)
        start = (now.tz_convert("UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def go():
            rows, params = [], {"timeframe": "5Min", "start": start, "limit": 10000, "adjustment": "raw",
                                "feed": "iex", "sort": "asc"}
            while True:
                r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars", params=params,
                                 headers=self.h, timeout=30)
                r.raise_for_status()
                j = r.json()
                rows.extend(j.get("bars") or [])
                if not j.get("next_page_token"):
                    return rows
                params["page_token"] = j["next_page_token"]
        return parse_alpaca_bars(with_retry(go, breaker=self.breaker), now, 5)


def get_intraday(settings: Settings, symbol: str | None = None, days: int = 30, now: pd.Timestamp | None = None):
    """Primary source with automatic fallback. Returns (df, source_name, capability_name)."""
    import os
    sym = symbol or settings.symbol
    if settings.primary_source == "alpaca_iex":
        try:
            p = AlpacaProvider(os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY"), settings)
            return p.intraday(sym, days, now), "alpaca_iex", AlpacaProvider.capability
        except Exception:  # noqa: BLE001  fall through to Yahoo
            pass
    y = YahooProvider()
    return y.intraday(sym, "5m", "60d" if days > 7 else "5d", now), "yahoo", YahooProvider.capability


# ------------------------------------------------------- Alpaca (indicative)

OPTION_COLS = ["contract_id", "underlying", "expiry", "strike", "right", "bid", "ask", "quote_ts",
               "delta", "gamma", "theta", "vega", "iv", "volume", "oi", "source", "quote_class"]


def parse_alpaca_snapshots(payload: dict, underlying: str) -> pd.DataFrame:
    """Parse Alpaca option snapshots. quote_class is ALWAYS 'indicative' (never executable).
    UNTESTED against the live API: field names follow Alpaca's documented snapshot shape."""
    rows = []
    for sym, snap in (payload.get("snapshots") or {}).items():
        q = snap.get("latestQuote") or {}
        g = snap.get("greeks") or {}
        occ = parse_occ(sym)
        if occ is None:
            continue
        rows.append({"contract_id": sym, "underlying": occ["root"], "expiry": occ["expiry"], "strike": occ["strike"],
                     "right": occ["right"], "bid": q.get("bp"), "ask": q.get("ap"), "quote_ts": q.get("t"),
                     "delta": g.get("delta"), "gamma": g.get("gamma"), "theta": g.get("theta"), "vega": g.get("vega"),
                     "iv": snap.get("impliedVolatility"), "volume": None, "oi": None,
                     "source": "alpaca_options_indicative", "quote_class": "indicative"})
    return pd.DataFrame(rows, columns=OPTION_COLS)


def parse_occ(sym: str) -> dict | None:
    """OCC symbol: ROOT (1-6 letters) + YYMMDD + C/P + strike*1000 (8 digits). e.g. SPY261013C00680000."""
    import re
    m = re.fullmatch(r"([A-Z]{1,6})(\d{6})([CP])(\d{8})", sym)
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return {"root": root, "expiry": pd.Timestamp(f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}").date(),
            "right": "call" if cp == "C" else "put", "strike": int(k) / 1000.0}


def fetch_alpaca_indicative(underlying: str, key_id: str, secret: str, settings: Settings, timeout: int = 20):
    """Opt-in, free. Returns research-only indicative quotes. Requires your own free Alpaca keys."""
    assert_free("alpaca_options_indicative", settings)
    import requests
    r = requests.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{underlying}",
                     params={"feed": "indicative", "limit": 1000},
                     headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}, timeout=timeout)
    r.raise_for_status()
    return parse_alpaca_snapshots(r.json(), underlying)
