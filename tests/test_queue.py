"""Durable ingestion-queue state machine on a temp DB: ticker normalization,
enqueue dedup, worker status transitions, attach, delete/clear."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from alphamaxx.data import (
    attach_ingestion_queue_company,
    clear_failed_ingestion_queue,
    count_pending_queue,
    delete_ingestion_queue_item,
    enqueue_ingestion,
    enqueue_ticker,
    get_ingestion_queue,
    get_queued_ingestions,
    mark_ingestion_queue_status,
)
from alphamaxx.data.queue import _normalize_queue_ticker


@pytest.fixture(autouse=True)
def clean_queue(db):
    db.execute("DELETE FROM ingestion_queue")
    yield


def test_normalize_queue_ticker():
    assert _normalize_queue_ticker(" aapl ") == "AAPL"
    assert _normalize_queue_ticker("brk.b") == "BRK.B"
    assert _normalize_queue_ticker("bf-b") == "BF-B"
    assert _normalize_queue_ticker("") == ""
    assert _normalize_queue_ticker("$SPY") == ""          # leading symbol
    assert _normalize_queue_ticker("WAY" + "X" * 20) == ""  # too long


def test_enqueue_ticker_and_dedupe(db):
    ok, msg = enqueue_ticker("nvda")
    assert ok and "NVDA" in msg
    ok2, msg2 = enqueue_ticker("NVDA")
    assert not ok2 and "already queued" in msg2
    assert count_pending_queue() == 1

    (item,) = get_queued_ingestions()
    assert item["requested_ticker"] == "NVDA"
    assert item["permno"] is None  # ticker-first: resolved by the worker


def test_concurrent_enqueue_is_serialized_and_deduplicated(db):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _i: enqueue_ticker("sync"), range(8)))
    assert sum(1 for ok, _message in results if ok) == 1
    assert count_pending_queue() == 1


def test_enqueue_ticker_rejects_garbage(db):
    ok, msg = enqueue_ticker("not a ticker!!")
    assert not ok and "valid US ticker" in msg
    assert count_pending_queue() == 0


def test_enqueue_ingestion_dedupes_by_permno_and_ticker(db, synthetic_company):
    assert enqueue_ingestion(synthetic_company) is True
    assert enqueue_ingestion(synthetic_company) is False       # same permno
    ok, _ = enqueue_ticker("TEST")                             # same ticker
    assert not ok
    assert count_pending_queue() == 1


def test_attach_then_status_transitions(db):
    enqueue_ticker("msft")
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) VALUES (888001, 'MSFT', 'Microsoft')"
    )
    attach_ingestion_queue_company("MSFT", 888001, "MSFT", "Microsoft Corp")

    (item,) = get_queued_ingestions()
    assert item["permno"] == 888001 and item["name"] == "Microsoft Corp"

    mark_ingestion_queue_status(888001, "running")
    (row,) = get_ingestion_queue()
    assert row["status"] == "running" and row["started_at"] is not None

    mark_ingestion_queue_status(888001, "complete")
    (row,) = get_ingestion_queue()
    assert row["status"] == "complete" and row["completed_at"] is not None
    assert count_pending_queue() == 0


def test_failed_by_ticker_then_clear(db):
    enqueue_ticker("zzzz")
    mark_ingestion_queue_status(None, "failed", "WRDS says no", ticker="zzzz")
    (row,) = get_ingestion_queue()
    assert row["status"] == "failed" and row["error"] == "WRDS says no"

    assert clear_failed_ingestion_queue() == 1
    assert get_ingestion_queue() == []


def test_delete_single_item_by_exact_timestamp(db):
    enqueue_ticker("qqq")
    (row,) = get_ingestion_queue()
    assert delete_ingestion_queue_item("qqq", str(row["queued_at"])) == 1
    assert get_ingestion_queue() == []
    # wrong timestamp deletes nothing
    enqueue_ticker("spy")
    assert delete_ingestion_queue_item("spy", "1999-01-01 00:00:00") == 0
    assert count_pending_queue() == 1
