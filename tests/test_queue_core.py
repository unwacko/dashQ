from datetime import datetime, timedelta

import pytest

from queue_core import DONE, NO_SHOW, SERVING, WAITING, QueueSystem, clean_phone


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 16, 10, 0, 0)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def qs(clock):
    return QueueSystem(":memory:", clock=clock)


@pytest.mark.parametrize("raw,expected", [
    ("98765 43210", "9876543210"),
    ("+91 98765-43210", "9876543210"),
    ("09876543210", "9876543210"),
    ("12345", None),
    ("5876543210", None),   # Indian mobiles start with 6-9
    ("", None),
])
def test_clean_phone(raw, expected):
    assert clean_phone(raw) == expected


def test_join_assigns_increasing_tokens(qs):
    a, _ = qs.join("9876543210", "A")
    b, _ = qs.join("9876543211", "B")
    assert (a.token, b.token) == (1, 2)
    assert a.status == WAITING


def test_rejects_invalid_and_duplicate_numbers(qs):
    assert qs.join("123")[1] is not None
    qs.join("9876543210")
    t, err = qs.join("+91 98765 43210")
    assert t is None and "already" in err


def test_priority_goes_first_then_fifo(qs, clock):
    a, _ = qs.join("9000000001", "A")
    clock.advance(1)
    b, _ = qs.join("9000000002", "B")
    clock.advance(1)
    c, _ = qs.join("9000000003", "C", priority=True)
    assert [t.name for t in qs.waiting()] == ["C", "A", "B"]


def test_call_next_moves_people_through(qs):
    a, _ = qs.join("9000000001", "A")
    b, _ = qs.join("9000000002", "B")
    assert qs.call_next(1).id == a.id
    assert qs.get(a.id).status == SERVING
    assert qs.call_next(1).id == b.id
    assert qs.get(a.id).status == DONE
    assert qs.call_next(1) is None
    assert qs.get(b.id).status == DONE


def test_no_show_and_cancel(qs):
    a, _ = qs.join("9000000001")
    b, _ = qs.join("9000000002")
    qs.call_next(1)
    qs.no_show(1)
    assert qs.get(a.id).status == NO_SHOW
    qs.cancel(b.id)
    assert qs.waiting() == []
    # once they've left, the same number can join again
    assert qs.join("9000000001")[0] is not None


def test_wait_estimate_single_counter(qs):
    tickets = [qs.join(f"900000000{i}")[0] for i in range(4)]
    # one counter, free: first person is called immediately
    assert qs.wait_minutes(tickets[0]) == 0
    assert qs.wait_minutes(tickets[1]) == 5
    qs.call_next(1)  # counter now busy
    assert qs.wait_minutes(tickets[1]) == 5
    assert qs.wait_minutes(tickets[3]) == 15


def test_wait_estimate_multiple_counters(qs):
    qs.set_setting("counters", 2)
    tickets = [qs.join(f"900000000{i}")[0] for i in range(6)]
    qs.call_next(1)
    qs.call_next(2)
    # 4 left waiting, two desks busy -> batches of two
    waits = [qs.wait_minutes(t) for t in tickets[2:]]
    assert waits == [5, 5, 10, 10]


def test_learns_real_service_time(qs, clock):
    for i in range(4):
        qs.join(f"900000000{i}")
    qs.call_next(1)
    for _ in range(3):
        clock.advance(2)        # desk takes 2 minutes per person
        qs.call_next(1)
    assert qs.avg_service_minutes() == pytest.approx(2.0)


def test_reminder_sent_once_when_close(qs):
    qs.set_setting("reminder_lead", 10)
    tickets = [qs.join(f"900000000{i}")[0] for i in range(5)]
    qs.call_next(1)
    qs.process_reminders()
    reminded = {t.id for t in qs.waiting() if t.reminded}
    # waits are 5,10,15,20 -> only the first two are within 10 minutes
    assert reminded == {tickets[1].id, tickets[2].id}
    assert qs.process_reminders() == 0  # no duplicates


def test_today_stats(qs, clock):
    qs.join("9000000001")
    qs.join("9000000002")
    clock.advance(4)
    qs.call_next(1)
    clock.advance(3)
    qs.call_next(1)
    s = qs.today_stats()
    assert s["joined"] == 2 and s["served"] == 1
    assert s["avg_service"] == pytest.approx(3.0)


def test_seed_demo_history(qs):
    assert qs.seed_demo_history(days=3) > 0
    assert qs.waiting() == []  # history never touches the live queue


def test_sample_data_loads_once(qs):
    assert qs.ensure_sample_data() is True
    assert [t.name for t in qs.waiting()] == ["Aarav", "Diya", "Kabir", "Meera", "Rohan"]
    assert len(qs.history()) > 5          # two weeks of past visits too
    assert qs.ensure_sample_data() is False  # not added twice
    qs.reset_all()
    assert qs.ensure_sample_data() is False  # stays empty after Clear
    assert qs.waiting() == []
