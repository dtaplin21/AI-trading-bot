"""
agents/news/news_ingestion_service.py

Pulls raw news and economic events from all approved sources
concurrently using asyncio. Nothing is scraped unless explicitly
enabled and the site's terms allow it. Every source produces
RawNewsItem objects that flow into the processing pipeline.

Source priority:
  1. Economic calendar APIs (FRED, FMP, Finnhub)
  2. Financial news APIs (Benzinga, Finnhub, Polygon, FMP, NewsAPI)
  3. RSS feeds (Reuters, CNBC, MarketWatch)
  4. Government public data (BLS, EIA, Treasury)
"""
from __future__ import annotations

import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from typing import Awaitable, Callable, Optional

import httpx

from agents.news.news_schemas import NewsSource, RawNewsItem

logger = logging.getLogger(__name__)

# ─── API Keys from environment (never hardcoded) ──────────────────────────────
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")
BENZINGA_KEY = os.getenv("BENZINGA_API_KEY", "")
POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")
FMP_KEY = os.getenv("FMP_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
MARKETAUX_KEY = os.getenv("MARKETAUX_API_KEY", "")
AV_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FRED_KEY = os.getenv("FRED_API_KEY", "")

HTTP_TIMEOUT = 10.0
MAX_RETRIES = 2
ARTICLES_LIMIT = 50

RSS_FEEDS = {
    "reuters_markets": "https://feeds.reuters.com/reuters/businessNews",
    "cnbc_markets": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "investing_com": "https://www.investing.com/rss/news.rss",
    "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
    "seeking_alpha": "https://seekingalpha.com/market_currents.xml",
    "ft_markets": "https://www.ft.com/rss/home/uk",
    "wsj_markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
}

WATCHED_SYMBOLS = [
    "ES", "MES", "NQ", "MNQ", "RTY", "YM", "MYM",
    "CL", "GC", "SI", "ZB", "ZN", "6E", "6J",
    "SPY", "QQQ", "TSLA", "AAPL", "NVDA", "MSFT", "AMZN", "META",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _source_requires_key(source_id: str) -> bool:
    key_map = {
        "finnhub_news": FINNHUB_KEY,
        "finnhub_calendar": FINNHUB_KEY,
        "benzinga": BENZINGA_KEY,
        "polygon": POLYGON_KEY,
        "fmp_news": FMP_KEY,
        "fmp_calendar": FMP_KEY,
        "newsapi": NEWSAPI_KEY,
        "marketaux": MARKETAUX_KEY,
        "alpha_vantage": AV_KEY,
        "eia_petroleum": os.getenv("EIA_API_KEY", ""),
        "fred_releases": FRED_KEY,
    }
    if source_id not in key_map:
        return False
    return not key_map[source_id]


class NewsIngestionService:
    """Collects raw news items from all enabled sources concurrently."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._client: Optional[httpx.AsyncClient] = None
        self._source_registry: dict[str, Callable[[], Awaitable[list[RawNewsItem]]]] = {}

    def _ensure_registry(self) -> None:
        if self._source_registry:
            return
        self._source_registry = {
            "finnhub_news": self._fetch_finnhub_news,
            "finnhub_calendar": self._fetch_finnhub_calendar,
            "benzinga": self._fetch_benzinga,
            "polygon": self._fetch_polygon,
            "fmp_news": self._fetch_fmp_news,
            "fmp_calendar": self._fetch_fmp_calendar,
            "newsapi": self._fetch_newsapi,
            "marketaux": self._fetch_marketaux,
            "alpha_vantage": self._fetch_alpha_vantage,
            "fred_releases": self._fetch_fred_releases,
            "eia_petroleum": self._fetch_eia_petroleum,
        }
        for name, url in RSS_FEEDS.items():
            self._source_registry[f"rss_{name}"] = partial(self._fetch_rss, name, url)

    def list_source_ids(self) -> list[str]:
        self._ensure_registry()
        return sorted(self._source_registry.keys())

    async def ingest_sources(self, source_ids: list[str]) -> list[RawNewsItem]:
        """Fetch only the requested sources (calendar pinpoint polling)."""
        self._ensure_registry()
        tasks: list = []
        for sid in source_ids:
            if sid not in self._source_registry:
                logger.warning("NewsIngestion: unknown source_id %s", sid)
                continue
            if _source_requires_key(sid):
                logger.debug("NewsIngestion: skipping %s — API key not set", sid)
                continue
            tasks.append(self._source_registry[sid]())
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._merge_results(results, allow_dev_fallback=False)

    async def ingest_all(self) -> list[RawNewsItem]:
        """Fire all source fetchers concurrently, deduplicate, sort newest first."""
        self._ensure_registry()
        tasks = [
            fn()
            for sid, fn in self._source_registry.items()
            if not _source_requires_key(sid)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._merge_results(results, allow_dev_fallback=True)

    def _merge_results(self, results: list, *, allow_dev_fallback: bool) -> list[RawNewsItem]:
        items: list[RawNewsItem] = []
        for batch in results:
            if isinstance(batch, Exception):
                logger.warning("Ingestion source failed: %s", batch)
                continue
            for item in batch:
                if not item.headline:
                    continue
                key = self._dedup_key(item)
                if key not in self._seen:
                    self._seen.add(key)
                    items.append(item)

        if allow_dev_fallback and not items and os.getenv("APP_ENV", "development") == "development":
            items = self._dev_fallback_items()

        items.sort(key=lambda x: x.published_at, reverse=True)
        logger.info("NewsIngestion: collected %d unique items", len(items))
        return items

    async def __aenter__(self) -> NewsIngestionService:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT),
            follow_redirects=True,
            headers={"User-Agent": "TradingAI-NewsAgent/1.0"},
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    def _dedup_key(self, item: RawNewsItem) -> str:
        return f"{item.source.value}|{item.headline[:80]}|{item.published_at.date()}"

    def _dev_fallback_items(self) -> list[RawNewsItem]:
        """Offline/dev fallback when no sources return data."""
        now = _utc_now()
        return [
            RawNewsItem(
                source=NewsSource.RSS,
                headline="Fed officials signal data-dependent rate path",
                summary="Markets watch upcoming CPI release for policy clues.",
                url="mock://dev/1",
                published_at=now,
                raw_payload={"feed": "dev_fallback", "mock": True},
            ),
            RawNewsItem(
                source=NewsSource.RSS,
                headline="S&P 500 futures edge higher ahead of economic data",
                summary="Index futures rise as traders await jobs report.",
                url="mock://dev/2",
                published_at=now,
                raw_payload={"feed": "dev_fallback", "mock": True},
            ),
        ]

    async def _fetch_finnhub_news(self) -> list[RawNewsItem]:
        items = []
        url = "https://finnhub.io/api/v1/news"
        params = {"category": "general", "token": FINNHUB_KEY}
        try:
            r = await self._get(url, params)
            if not isinstance(r, list):
                return items
            for article in r[:ARTICLES_LIMIT]:
                published = datetime.fromtimestamp(article.get("datetime", 0), tz=timezone.utc)
                items.append(
                    RawNewsItem(
                        source=NewsSource.FINNHUB,
                        headline=article.get("headline", ""),
                        summary=article.get("summary"),
                        url=article.get("url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("Finnhub news error: %s", e)
        return items

    async def _fetch_finnhub_calendar(self) -> list[RawNewsItem]:
        items = []
        today = _utc_now().strftime("%Y-%m-%d")
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {"token": FINNHUB_KEY, "from": today, "to": today}
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for event in r.get("economicCalendar") or []:
                time_raw = event.get("time") or _utc_now().isoformat()
                try:
                    published = datetime.fromisoformat(str(time_raw).replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except Exception:
                    published = _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.FINNHUB,
                        headline=event.get("event", "Economic event"),
                        summary=f"Forecast: {event.get('estimate')} | Previous: {event.get('prev')}",
                        published_at=published,
                        raw_payload=event,
                    )
                )
        except Exception as e:
            logger.error("Finnhub calendar error: %s", e)
        return items

    async def _fetch_benzinga(self) -> list[RawNewsItem]:
        items = []
        url = "https://api.benzinga.com/api/v2/news"
        params = {
            "token": BENZINGA_KEY,
            "pageSize": ARTICLES_LIMIT,
            "displayOutput": "abstract",
            "tickers": ",".join(WATCHED_SYMBOLS),
        }
        try:
            r = await self._get(url, params)
            articles = r if isinstance(r, list) else []
            for article in articles:
                published_raw = article.get("created") or article.get("updated") or ""
                try:
                    published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                except Exception:
                    published = _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.BENZINGA,
                        headline=article.get("title", ""),
                        summary=article.get("teaser"),
                        url=article.get("url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("Benzinga error: %s", e)
        return items

    async def _fetch_polygon(self) -> list[RawNewsItem]:
        items = []
        url = "https://api.polygon.io/v2/reference/news"
        params = {
            "apiKey": POLYGON_KEY,
            "limit": ARTICLES_LIMIT,
            "order": "desc",
            "sort": "published_utc",
            "ticker.any_of": ",".join(WATCHED_SYMBOLS),
        }
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for article in r.get("results") or []:
                pub = article.get("published_utc", "")
                published = datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.POLYGON,
                        headline=article.get("title", ""),
                        summary=article.get("description"),
                        url=article.get("article_url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("Polygon error: %s", e)
        return items

    async def _fetch_fmp_news(self) -> list[RawNewsItem]:
        items = []
        url = "https://financialmodelingprep.com/api/v3/stock_news"
        params = {"apikey": FMP_KEY, "limit": ARTICLES_LIMIT}
        try:
            r = await self._get(url, params)
            articles = r if isinstance(r, list) else []
            for article in articles:
                pub = article.get("publishedDate")
                if pub:
                    published = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                else:
                    published = _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.FMP,
                        headline=article.get("title", ""),
                        summary=(article.get("text") or "")[:500] or None,
                        url=article.get("url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("FMP news error: %s", e)
        return items

    async def _fetch_fmp_calendar(self) -> list[RawNewsItem]:
        items = []
        today = _utc_now().strftime("%Y-%m-%d")
        url = "https://financialmodelingprep.com/api/v3/economic_calendar"
        params = {"apikey": FMP_KEY, "from": today, "to": today}
        try:
            r = await self._get(url, params)
            events = r if isinstance(r, list) else []
            for event in events:
                date_raw = event.get("date") or _utc_now().isoformat()
                try:
                    published = datetime.fromisoformat(str(date_raw).replace("Z", "+00:00"))
                except Exception:
                    published = _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.FMP,
                        headline=event.get("event", "Economic event"),
                        summary=(
                            f"Impact: {event.get('impact', 'unknown')} | "
                            f"Forecast: {event.get('estimate')} | "
                            f"Previous: {event.get('previous')}"
                        ),
                        published_at=published,
                        raw_payload=event,
                    )
                )
        except Exception as e:
            logger.error("FMP calendar error: %s", e)
        return items

    async def _fetch_newsapi(self) -> list[RawNewsItem]:
        items = []
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            "apiKey": NEWSAPI_KEY,
            "category": "business",
            "language": "en",
            "pageSize": ARTICLES_LIMIT,
        }
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for article in r.get("articles") or []:
                pub = article.get("publishedAt")
                published = datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.NEWSAPI,
                        headline=article.get("title", ""),
                        summary=article.get("description"),
                        url=article.get("url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("NewsAPI error: %s", e)
        return items

    async def _fetch_marketaux(self) -> list[RawNewsItem]:
        items = []
        url = "https://api.marketaux.com/v1/news/all"
        params = {
            "api_token": MARKETAUX_KEY,
            "symbols": ",".join(WATCHED_SYMBOLS[:10]),
            "limit": ARTICLES_LIMIT,
            "language": "en",
        }
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for article in r.get("data") or []:
                pub = article.get("published_at")
                published = datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.MARKETAUX,
                        headline=article.get("title", ""),
                        summary=article.get("description"),
                        url=article.get("url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("MarketAux error: %s", e)
        return items

    async def _fetch_alpha_vantage(self) -> list[RawNewsItem]:
        items = []
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "NEWS_SENTIMENT",
            "apikey": AV_KEY,
            "tickers": ",".join(WATCHED_SYMBOLS[:10]),
            "limit": ARTICLES_LIMIT,
            "sort": "LATEST",
        }
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for article in r.get("feed") or []:
                published_raw = article.get("time_published", "")
                try:
                    published = datetime.strptime(published_raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                except Exception:
                    published = _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.ALPHA_VANTAGE,
                        headline=article.get("title", ""),
                        summary=article.get("summary"),
                        url=article.get("url"),
                        published_at=published,
                        raw_payload=article,
                    )
                )
        except Exception as e:
            logger.error("Alpha Vantage error: %s", e)
        return items

    async def _fetch_fred_releases(self) -> list[RawNewsItem]:
        items = []
        url = "https://api.stlouisfed.org/fred/releases/dates"
        params = {
            "api_key": FRED_KEY or "abcdefghijklmnopqrstuvwxyz123456",
            "file_type": "json",
            "include_release_dates_with_no_data": "true" if FRED_KEY else "false",
            "realtime_start": _utc_now().strftime("%Y-%m-%d"),
            "realtime_end": (_utc_now() + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for release in (r.get("release_dates") or [])[:20]:
                items.append(
                    RawNewsItem(
                        source=NewsSource.FRED,
                        headline=f"FRED Release: {release.get('release_name', 'Economic data')}",
                        summary=f"Release date: {release.get('date')}",
                        published_at=_utc_now(),
                        raw_payload=release,
                    )
                )
        except Exception as e:
            logger.error("FRED error: %s", e)
        return items

    async def _fetch_eia_petroleum(self) -> list[RawNewsItem]:
        items = []
        eia_key = os.getenv("EIA_API_KEY", "")
        if not eia_key:
            return items
        url = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"
        params = {
            "api_key": eia_key,
            "frequency": "weekly",
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 1,
        }
        try:
            r = await self._get(url, params)
            if not isinstance(r, dict):
                return items
            for row in (r.get("response", {}).get("data") or [])[:1]:
                items.append(
                    RawNewsItem(
                        source=NewsSource.EIA,
                        headline=f"EIA Petroleum Inventory: {row.get('value')} {row.get('units', '')}",
                        summary=f"Period: {row.get('period')} | Product: {row.get('product-name', 'crude oil')}",
                        published_at=_utc_now(),
                        raw_payload=row,
                    )
                )
        except Exception as e:
            logger.error("EIA error: %s", e)
        return items

    async def _fetch_rss(self, feed_name: str, feed_url: str) -> list[RawNewsItem]:
        items = []
        if not self._client:
            return items
        try:
            r = await self._client.get(feed_url)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            channel = root.find("channel") or root
            for entry in list(channel.findall("item"))[:ARTICLES_LIMIT]:
                title = (entry.findtext("title") or "").strip()
                link = (entry.findtext("link") or "").strip()
                pub_raw = (entry.findtext("pubDate") or "").strip()
                desc = (entry.findtext("description") or "").strip()
                if not title:
                    continue
                try:
                    published = parsedate_to_datetime(pub_raw) if pub_raw else _utc_now()
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except Exception:
                    published = _utc_now()
                items.append(
                    RawNewsItem(
                        source=NewsSource.RSS,
                        headline=title,
                        summary=desc[:300] or None,
                        url=link or None,
                        published_at=published,
                        raw_payload={"feed": feed_name},
                    )
                )
        except Exception as e:
            logger.warning("RSS feed %s failed: %s", feed_name, e)
        return items

    async def _get(self, url: str, params: dict) -> dict | list:
        if not self._client:
            raise RuntimeError("HTTP client not initialized")
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = await self._client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = 2**attempt
                    logger.warning("Rate limited by %s, waiting %ds", url, wait)
                    await asyncio.sleep(wait)
                else:
                    raise
            except httpx.RequestError:
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(1)
        return {}
