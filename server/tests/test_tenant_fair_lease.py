"""Unit tests for #34 (tenant-fair lease_batch) and #36 (tenant_cursors)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import send_queue_service as sqs


def _make_fake_db(
    *,
    cursor_doc: dict | None,
    distinct_result: list[str],
    lease_sequence: list[dict | None],
) -> MagicMock:
    """Build an AsyncMock that mimics the three Mongo calls
    `_next_tenant_in_round_robin` and `lease_batch` make:
      - tenant_cursors.find_one_and_update  (reads/inits the cursor)
      - send_queue.distinct                 (list of tenants with work)
      - tenant_cursors.update_one           (persist the advance)
      - send_queue.find_one_and_update      (leases docs one-by-one)
    """
    db = MagicMock()
    db.tenant_cursors = MagicMock()
    db.tenant_cursors.find_one_and_update = AsyncMock(return_value=cursor_doc)
    db.tenant_cursors.update_one = AsyncMock(return_value=None)
    db.send_queue = MagicMock()
    db.send_queue.distinct = AsyncMock(return_value=distinct_result)
    db.send_queue.find_one_and_update = AsyncMock(side_effect=lease_sequence)
    return db


# ── _next_tenant_in_round_robin ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_next_tenant_picks_first_when_cursor_empty() -> None:
    db = _make_fake_db(
        cursor_doc={"_id": "global"},  # no last_tenant_id yet
        distinct_result=["t-b", "t-a", "t-c"],  # shuffle to verify sort
        lease_sequence=[],
    )
    result = await sqs._next_tenant_in_round_robin(db, active_campaign_ids=["c1"])
    assert result == "t-a"  # sorted first
    # and the cursor was persisted
    db.tenant_cursors.update_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_next_tenant_advances_strictly_past_last() -> None:
    db = _make_fake_db(
        cursor_doc={"_id": "global", "last_tenant_id": "t-a"},
        distinct_result=["t-a", "t-b", "t-c"],
        lease_sequence=[],
    )
    result = await sqs._next_tenant_in_round_robin(db, active_campaign_ids=["c1"])
    assert result == "t-b"


@pytest.mark.asyncio
async def test_next_tenant_wraps_around_at_end() -> None:
    db = _make_fake_db(
        cursor_doc={"_id": "global", "last_tenant_id": "t-c"},
        distinct_result=["t-a", "t-b", "t-c"],
        lease_sequence=[],
    )
    result = await sqs._next_tenant_in_round_robin(db, active_campaign_ids=["c1"])
    assert result == "t-a"  # wrapped


@pytest.mark.asyncio
async def test_next_tenant_returns_none_when_no_work() -> None:
    db = _make_fake_db(
        cursor_doc={"_id": "global"},
        distinct_result=[],
        lease_sequence=[],
    )
    result = await sqs._next_tenant_in_round_robin(db, active_campaign_ids=["c1"])
    assert result is None
    db.tenant_cursors.update_one.assert_not_awaited()


# ── lease_batch ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lease_batch_empty_campaigns_short_circuits() -> None:
    db = MagicMock()
    result = await sqs.lease_batch(db, "agent-1", [], batch_size=5)
    assert result == []


@pytest.mark.asyncio
async def test_lease_batch_leases_only_from_picked_tenant() -> None:
    fake_doc = {"_id": "x", "tenant_user_id": "t-a", "campaign_id": "c1"}
    db = _make_fake_db(
        cursor_doc={"_id": "global"},
        distinct_result=["t-a", "t-b"],
        lease_sequence=[fake_doc, fake_doc, None],  # 2 docs then exhausted
    )
    result = await sqs.lease_batch(db, "agent-1", ["c1"], batch_size=5)
    assert len(result) == 2
    # Every call filtered by the round-robin-picked tenant
    for call in db.send_queue.find_one_and_update.await_args_list:
        query = call.args[0]
        assert query["tenant_user_id"] == "t-a"
        assert query["status"] == "pending"


@pytest.mark.asyncio
async def test_lease_batch_respects_batch_size() -> None:
    fake_doc = {"_id": "x", "tenant_user_id": "t-a", "campaign_id": "c1"}
    db = _make_fake_db(
        cursor_doc={"_id": "global"},
        distinct_result=["t-a"],
        lease_sequence=[fake_doc] * 10,  # always returns a doc
    )
    result = await sqs.lease_batch(db, "agent-1", ["c1"], batch_size=3)
    assert len(result) == 3  # stopped at batch_size
