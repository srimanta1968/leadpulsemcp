"""Unit tests for #25: pending_crm_events backpressure threshold."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import pending_crm_events


def _fake_db(fill_count: int) -> MagicMock:
    db = MagicMock()
    db.pending_crm_events = MagicMock()
    db.pending_crm_events.estimated_document_count = AsyncMock(return_value=fill_count)
    return db


@pytest.mark.asyncio
async def test_backpressure_false_below_threshold() -> None:
    db = _fake_db(fill_count=0)
    assert await pending_crm_events.is_under_backpressure(db) is False

    db = _fake_db(fill_count=int(5_000_000 * 0.5))  # 50%
    assert await pending_crm_events.is_under_backpressure(db) is False


@pytest.mark.asyncio
async def test_backpressure_false_just_under_threshold() -> None:
    db = _fake_db(fill_count=int(5_000_000 * 0.8) - 1)
    assert await pending_crm_events.is_under_backpressure(db) is False


@pytest.mark.asyncio
async def test_backpressure_true_at_threshold() -> None:
    db = _fake_db(fill_count=int(5_000_000 * 0.8))
    assert await pending_crm_events.is_under_backpressure(db) is True


@pytest.mark.asyncio
async def test_backpressure_true_above_threshold() -> None:
    db = _fake_db(fill_count=5_000_000)
    assert await pending_crm_events.is_under_backpressure(db) is True
