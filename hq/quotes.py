"""Latest prices for the watchlist scorecard (Yahoo, free), cached for a few minutes."""

from __future__ import annotations

import threading
import time

CACHE_SECONDS = 600
_lock = threading.Lock()
_cache: dict[str, tuple[float, float]] = {}   # ticker -> (fetched_at, price)


def latest(tickers: list[str]) -> dict[str, float | None]:
    """Last close for each ticker (None when Yahoo has nothing)."""
    now = time.time()
    with _lock:
        fresh = {t: p for t, (ts, p) in _cache.items() if t in tickers and now - ts < CACHE_SECONDS}
    missing = [t for t in tickers if t not in fresh]
    if missing:
        fetched = _download(missing)
        with _lock:
            for t, p in fetched.items():
                if p is not None:
                    _cache[t] = (now, p)
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
