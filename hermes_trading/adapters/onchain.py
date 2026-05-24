"""On-chain adapter — fetches basic BTC metrics via Glassnode free tier or CoinGecko fallback."""
import os
import httpx

SCHEMA_VERSION = "onchain/1"


class SchemaError(Exception):
    pass


async def fetch(asset: str = "BTC/USDT") -> dict:
    glassnode_key = os.getenv("GLASSNODE_API_KEY", "")
    coin = asset.split("/")[0].lower()

    if glassnode_key:
        return await _fetch_glassnode(coin, glassnode_key)
    return await _fetch_coingecko(coin)


async def _fetch_glassnode(coin: str, api_key: str) -> dict:
    url = "https://api.glassnode.com/v1/metrics/market/price_usd_close"
    params = {"a": coin.upper(), "api_key": api_key, "i": "24h", "limit": 1}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if not data or "v" not in data[0]:
        raise SchemaError("Unexpected Glassnode schema")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "glassnode",
        "price_usd": data[0]["v"],
    }


async def _fetch_coingecko(coin: str) -> dict:
    slug_map = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana"}
    slug = slug_map.get(coin, coin)
    url = f"https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": slug, "vs_currencies": "usd", "include_market_cap": "true"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if slug not in data:
        raise SchemaError(f"CoinGecko returned no data for {slug}")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "coingecko",
        "price_usd": data[slug]["usd"],
        "market_cap_usd": data[slug].get("usd_market_cap"),
    }
