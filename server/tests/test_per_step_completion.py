"""Per-step completion check for send_queue_service.

Locks the fix where any_pending_for_campaign_step must scope its
count_documents query to a single (campaign_id, step_index). Without
this scoping, step-complete never posts on multi-step sequences
because step N+1 docs are pre-enqueued at ingest with future
scheduled_for and always look "pending" to a cross-step check.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import send_queue_service as sqs


def _fake_db(count_return: int) -> MagicMock:
    db = MagicMock()
    db.send_queue = MagicMock()
    db.send_queue.count_documents = AsyncMock(return_value=count_return)
    return db


@pytest.mark.asyncio
async def test_any_pending_for_campaign_step_uses_step_filter() -> None:
    db = _fake_db(count_return=0)
    result = await sqs.any_pending_for_campaign_step(db, "c1", 0)

    # The query must include step_index — otherwise pending docs for step 1
    # would mask completion of step 0 and step-complete would never post.
    args, kwargs = db.send_queue.count_documents.call_args
    query = args[0]
    assert query["campaign_id"] == "c1"
    assert query["step_index"] == 0
    assert query["status"] == {"$in": ["pending", "leased"]}
    assert kwargs.get("limit") == 1
    assert result is False


@pytest.mark.asyncio
async def test_any_pending_for_campaign_step_returns_true_when_step_has_pending() -> None:
    db = _fake_db(count_return=3)
    result = await sqs.any_pending_for_campaign_step(db, "c1", 1)
    assert result is True


@pytest.mark.asyncio
async def test_any_pending_for_campaign_step_coerces_step_index_to_int() -> None:
    db = _fake_db(count_return=0)
    await sqs.any_pending_for_campaign_step(db, "c1", "2")  # type: ignore[arg-type]
    args, _kwargs = db.send_queue.count_documents.call_args
    assert args[0]["step_index"] == 2
