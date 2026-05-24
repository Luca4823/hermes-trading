"""Macro adapter — fetches DXY and US10Y as regime context via yfinance."""
import asyncio
import yfinance as yf

SCHEMA_VERSION = "macro/1"


class SchemaError(Exception):
    pass


async def fetch(asset: str = "BTC/USDT") -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch_sync)
    return result


def _fetch_sync() -> dict:
    dxy = yf.Ticker("DX-Y.NYB")
    us10y = yf.Ticker("^TNX")

    dxy_hist = dxy.history(period="2d")
    us10y_hist = us10y.history(period="2d")

    if dxy_hist.empty or us10y_hist.empty:
        raise SchemaError("yfinance returned empty macro data")

    return {
        "schema_version": SCHEMA_VERSION,
        "dxy_last": float(dxy_hist["Close"].iloc[-1]),
        "us10y_last": float(us10y_hist["Close"].iloc[-1]),
        "dxy_1d_chg": float(dxy_hist["Close"].pct_change().iloc[-1]),
        "us10y_1d_chg": float(us10y_hist["Close"].pct_change().iloc[-1]),
    }
