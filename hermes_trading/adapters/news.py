"""News adapter — fetches recent headlines via NewsAPI or CryptoPanic free tier."""
import os
import httpx

SCHEMA_VERSION = "news/1"


class SchemaError(Exception):
    pass


async def fetch(asset: str = "BTC/USDT") -> dict:
    news_api_key = os.getenv("NEWS_API_KEY", "")
    coin = asset.split("/")[0]

    if news_api_key:
        return await _fetch_newsapi(coin, news_api_key)
    return await _fetch_cryptopanic(coin)


async def _fetch_newsapi(coin: str, api_key: str) -> dict:
    url = "https://newsapi.org/v2/everything"
    params = {"q": coin, "sortBy": "publishedAt", "pageSize": 5, "apiKey": api_key}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if "articles" not in data:
        raise SchemaError("Unexpected NewsAPI schema")
    headlines = [a["title"] for a in data["articles"] if a.get("title")]
    return {"schema_version": SCHEMA_VERSION, "source": "newsapi", "headlines": headlines}


async def _fetch_cryptopanic(coin: str) -> dict:
    url = "https://cryptopanic.com/api/v1/posts/"
    params = {"auth_token": "free", "currencies": coin, "public": "true"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        results = data.get("results", [])
        headlines = [item.get("title", "") for item in results[:5]]
    except Exception:
        headlines = []
    return {"schema_version": SCHEMA_VERSION, "source": "cryptopanic", "headlines": headlines}
