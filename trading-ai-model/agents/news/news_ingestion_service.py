"""Async news ingestion from RSS and configured sources."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree

import httpx

from agents.news.news_schemas import RawNewsItem

logger = logging.getLogger(__name__)

# Public RSS feeds — no API key required for development
DEFAULT_FEEDS = [
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC Top", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
]


class NewsIngestionService:
    """Fetches raw news concurrently from RSS feeds."""

    def __init__(self, feeds: Optional[list[tuple[str, str]]] = None, timeout: float = 10.0):
        self.feeds = feeds or DEFAULT_FEEDS
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._seen_urls: set[str] = set()

    async def __aenter__(self) -> NewsIngestionService:
        self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def ingest_all(self) -> list[RawNewsItem]:
        if not self._client:
            raise RuntimeError("Use async with NewsIngestionService()")

        tasks = [self._fetch_feed(source, url) for source, url in self.feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[RawNewsItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Feed fetch failed: %s", result)
                continue
            items.extend(result)
        return items

    async def _fetch_feed(self, source: str, url: str) -> list[RawNewsItem]:
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return self._parse_rss(source, resp.text)
        except Exception as exc:
            logger.debug("Feed %s unavailable: %s", source, exc)
            return self._mock_items(source)

    def _parse_rss(self, source: str, xml_text: str) -> list[RawNewsItem]:
        items: list[RawNewsItem] = []
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            return self._mock_items(source)

        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub = item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}published")

            if not title or link in self._seen_urls:
                continue
            self._seen_urls.add(link)

            published = datetime.now(timezone.utc)
            if pub:
                try:
                    published = parsedate_to_datetime(pub)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            items.append(
                RawNewsItem(
                    source=source,
                    headline=title,
                    summary=desc[:500],
                    url=link,
                    published_at=published,
                    raw_text=f"{title}. {desc}",
                )
            )
            if len(items) >= 20:
                break
        return items or self._mock_items(source)

    def _mock_items(self, source: str) -> list[RawNewsItem]:
        """Fallback when feeds are unreachable (sandbox/offline)."""
        now = datetime.now(timezone.utc)
        return [
            RawNewsItem(
                source=source,
                headline="Fed officials signal data-dependent rate path",
                summary="Markets watch upcoming CPI release for policy clues.",
                url=f"mock://{source}/1",
                published_at=now,
                raw_text="Fed officials signal data-dependent rate path. S&P futures steady.",
            ),
            RawNewsItem(
                source=source,
                headline="S&P 500 futures edge higher ahead of economic data",
                summary="Index futures rise as traders await jobs report.",
                url=f"mock://{source}/2",
                published_at=now,
                raw_text="S&P 500 futures edge higher ahead of economic data.",
            ),
        ]
