"""Latest prices for the watchlist scorecard (Yahoo, free), cached for a few minutes."""

from __future__ import annotations

import threading
import time

CACHE_SECONDS = 600
MISS_SECONDS = 120            # a ticker Yahoo had nothing for is not asked again for a while
_lock = threading.Lock()
_downloading = threading.Lock()   # one download at a time: callers queue and reuse its result
_cache: dict[str, tuple[float, float | None]] = {}   # ticker -> (fetched_at, price or None)


def _fresh(tickers: list[str]) -> dict[str, float | None]:
    now = time.time()
    with _lock:
        return {t: p for t, (ts, p) in _cache.items()
                if t in tickers and now - ts < (CACHE_SECONDS if p is not None else MISS_SECONDS)}


def latest(tickers: list[str]) -> dict[str, float | None]:
    """Latest price for each ticker (None when Yahoo has nothing). Public pages call this for
    every visitor, so results and misses are cached and concurrent callers share one download."""
    fresh = _fresh(tickers)
    if any(t not in fresh for t in tickers):
        with _downloading:
            fresh = _fresh(tickers)   # someone else may have fetched them while we waited
            missing = [t for t in tickers if t not in fresh]
            if missing:
                fetched = _download(missing)
                now = time.time()
                with _lock:
                    for t in missing:
                        _cache[t] = (now, fetched.get(t))
                fresh.update(fetched)
    return {t: fresh.get(t) for t in tickers}


def _download(tickers: list[str]) -> dict[str, float | None]:
    import yfinance as yf
    from erb import market

    symbols = {market._yf_symbol(t): t for t in tickers}
    try:
        df = yf.download(list(symbols), period="5d", auto_adjust=True, progress=False, threads=True)
    except Exception:  # noqa: BLE001 - any network/data failure: show no price, don't fail the page
        return {t: None for t in tickers}
    if df.empty:
        return {t: None for t in tickers}
    close = df["Close"]
    out = {}
    for sym, t in symbols.items():
        try:
            s = close[sym] if hasattr(close, "columns") and sym in close.columns else close
            out[t] = float(s.dropna().iloc[-1])
        except Exception:  # noqa: BLE001 - one missing ticker must not hide the rest
            out[t] = None
    return out
