"""Persistent research store: deduplicated bar history (csv.gz), JSON state, JSONL logs.

All writes are atomic (temp file + rename). Existing history is only ever extended, never reset.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

import pandas as pd

from .config import TZ, state_dir


def _safe(sym: str) -> str:
    return sym.replace("^", "").replace("=", "_").replace("/", "_")


def bars_path(symbol: str, interval: str, base: Path | None = None) -> Path:
    d = (base or state_dir()) / "bars"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe(symbol)}_{interval}.csv.gz"


def _atomic_write_bytes(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_bars(symbol: str, interval: str, base: Path | None = None) -> pd.DataFrame:
    p = bars_path(symbol, interval, base)
    if not p.exists():
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.read_csv(p, index_col=0)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert(TZ)
    return df.sort_index()


def save_bars(symbol: str, interval: str, new: pd.DataFrame, base: Path | None = None) -> dict:
    """Merge `new` into stored history. Newer rows win on duplicate timestamps. Returns counts."""
    old = load_bars(symbol, interval, base)
    before = len(old)
    if len(new):
        merged = pd.concat([old, new[["open", "high", "low", "close", "volume"]]])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = old
    p = bars_path(symbol, interval, base)
    if p.exists() and len(merged) < before:  # defence in depth: never shrink history
        raise RuntimeError("refusing to overwrite history with fewer rows")
    if p.exists():
        shutil.copy2(p, p.with_suffix(".bak"))  # one rolling backup
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(merged.to_csv().encode())
    _atomic_write_bytes(p, buf.getvalue())
    return {"before": before, "after": len(merged), "added": len(merged) - before}


def df_fingerprint(df: pd.DataFrame) -> str:
    if df is None or len(df) == 0:
        return "empty"
    h = hashlib.sha1(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()[:12]
    return f"{len(df)}rows:{df.index[0]}..{df.index[-1]}:{h}"


def write_json(path: Path, obj) -> None:
    _atomic_write_bytes(path, json.dumps(obj, indent=2, default=str).encode())


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def read_jsonl(path: Path, last_n: int | None = None) -> list[dict]:
    if not Path(path).exists():
        return []
    rows = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn last line after a crash
    return rows[-last_n:] if last_n else rows
