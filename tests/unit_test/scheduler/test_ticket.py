# tests/unit_test/scheduler/test_ticket.py
import pytest
from scheduler.ticket_queue.ticket import Ticket, POISON_PRIORITY


def test_ticket_comparison_lower_priority_wins():
    high = Ticket(priority=0, task_name="A", payload={})
    low = Ticket(priority=100, task_name="B", payload={})
    assert high < low


def test_ticket_poison_factory():
    p = Ticket.poison()
    assert p.priority == POISON_PRIORITY
    assert p.is_poison()
    assert p.task_name == "__POISON__"


def test_ticket_is_not_poison_for_normal():
    t = Ticket(priority=50, task_name="RANKING_UPDATE", payload={"date": "20250417"})
    assert not t.is_poison()


def test_ticket_created_at_set_automatically():
    t = Ticket(priority=50, task_name="X", payload={})
    assert t.created_at != ""


def test_ticket_payload_preserved():
    payload = {"date": "20250417", "extra": 42}
    t = Ticket(priority=10, task_name="TEST", payload=payload)
    assert t.payload["date"] == "20250417"
    assert t.payload["extra"] == 42
