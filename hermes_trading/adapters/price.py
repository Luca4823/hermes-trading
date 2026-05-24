"""Price adapter — fetches OHLCV + current price via ccxt (free public endpoints)."""
import os
import ccxt.async_support as ccxt

SCHEMA_VERSION = "price/1"


class SchemaError(Exception):
    pass


async def fetch(asset: str = "BTC/USDT") -> dict:
    exchange_id = os.getenv("EXCHANGE_ID", "binance")
    api_key = os.getenv("EXCHANGE_API_KEY", "")
    api_secret = os.getenv("EXCHANGE_API_SECRET", "")

    config = {}
    if api_key:
        config["apiKey"] = api_key
        config["secret"] = api_secret

    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class(config)

    try:
        ticker = await exchange.fetch_ticker(asset)
        ohlcv = await exchange.fetch_ohlcv(asset, timeframe="1h", limit=50)
    finally:
        await exchange.close()

    if not ticker or "last" not in ticker:
        raise SchemaError(f"Unexpected ticker schema from {exchange_id}")

    closes = [bar[4] for bar in ohlcv]

    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "last": ticker["last"],
        "bid": ticker["bid"],
        "ask": ticker["ask"],
        "volume_24h": ticker["baseVolume"],
        "closes_1h": closes,
    }
