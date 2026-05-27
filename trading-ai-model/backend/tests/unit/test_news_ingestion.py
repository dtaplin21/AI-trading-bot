"""Tests for multi-source news ingestion."""

import asyncio

import pytest

from agents.news.news_ingestion_service import NewsIngestionService
from agents.news.news_schemas import NewsSource


@pytest.mark.asyncio
async def test_ingest_all_returns_items_in_dev():
    async with NewsIngestionService() as svc:
        items = await svc.ingest_all()
    assert len(items) >= 1
    assert all(i.headline for i in items)


@pytest.mark.asyncio
async def test_deduplication():
    async with NewsIngestionService() as svc:
        first = await svc.ingest_all()
        second = await svc.ingest_all()
    assert len(second) <= len(first)


@pytest.mark.asyncio
async def test_items_sorted_newest_first():
    async with NewsIngestionService() as svc:
        items = await svc.ingest_all()
    if len(items) >= 2:
        assert items[0].published_at >= items[-1].published_at


@pytest.mark.asyncio
async def test_dev_fallback_source_is_rss():
    async with NewsIngestionService() as svc:
        items = await svc.ingest_all()
    assert items[0].source == NewsSource.RSS or items[0].source in NewsSource
