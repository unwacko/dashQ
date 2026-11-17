# queue_core.py
# all the queue logic for dashQ (no streamlit code in here, so the tests can use it too)
#
# ticket goes: waiting -> serving -> done
#   (no_show if they don't turn up, cancelled if they leave the queue)

from __future__ import annotations

import os
import random
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

# streamlit cloud runs on UTC so we always use india time (can change it with DASHQ_TZ)
LOCAL_TZ = ZoneInfo(os.environ.get("DASHQ_TZ", "Asia/Kolkata"))


def local_now() -> datetime:
    # current time in india, saved without the timezone part
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)

WAITING, SERVING, DONE, CANCELLED, NO_SHOW = "waiting", "serving", "done", "cancelled", "no_show"
ACTIVE = (WAITING, SERVING)

DEFAULT_SETTINGS = {
    "business_name": "City Clinic",
    "counters": "1",           # how many desks serve people at the same time
    "avg_minutes": "5",        # fallback service time until we have real data
    "reminder_lead": "10",     # send a "head back" SMS this many minutes before the turn
    "sample_loaded": "0",      # set to 1 once the sample data has been added on first start
    "public_url": "",          # address for the join QR code; blank = the address the app is opened at
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       INTEGER NOT NULL,          -- number shown to the customer, resets daily
    phone       TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'waiting',
    counter     INTEGER,                   -- desk that served this ticket
    joined_at   TEXT    NOT NULL,
    called_at   TEXT,
    finished_at TEXT,
    reminded    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    phone   TEXT NOT NULL,
    body    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Ticket:
    id: int
    token: int
    phone: str
    name: str
    priority: bool
    status: str
    counter: Optional[int]
    joined_at: datetime
    called_at: Optional[datetime]
    finished_at: Optional[datetime]
    reminded: bool

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Ticket":
        ts = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
        return cls(
            id=r["id"], token=r["token"], phone=r["phone"], name=r["name"],
            priority=bool(r["priority"]), status=r["status"], counter=r["counter"],
            joined_at=ts(r["joined_at"]), called_at=ts(r["called_at"]),
            finished_at=ts(r["finished_at"]), reminded=bool(r["reminded"]),
        )


def clean_phone(raw: str) -> Optional[str]:
    # returns the 10 digit number, or None if it's not a valid indian mobile no.
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


class QueueSystem:
    def __init__(self, db_path: str = "dashq.db", clock: Callable[[], datetime] = local_now):
        self.clock = clock  # injectable so tests can control time
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            self.db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        self.db.commit()

    # --- settings ---
    def get_setting(self, key: str) -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS[key]

    def set_setting(self, key: str, value) -> None:
        with self.lock:
            self.db.execute("REPLACE INTO settings(key, value) VALUES (?, ?)", (key, str(value)))
            self.db.commit()

    @property
    def business_name(self) -> str:
        return self.get_setting("business_name")

    @property
    def counters(self) -> int:
        return max(1, int(self.get_setting("counters")))

    @property
    def reminder_lead(self) -> int:
        return int(self.get_setting("reminder_lead"))

    # --- queries ---
    def _tickets(self, where: str = "1=1", params=()) -> list[Ticket]:
        rows = self.db.execute(f"SELECT * FROM tickets WHERE {where}", params).fetchall()
        return [Ticket.from_row(r) for r in rows]

    def get(self, ticket_id) -> Optional[Ticket]:
        if ticket_id is None:
            return None
        t = self._tickets("id = ?", (ticket_id,))
        return t[0] if t else None

    def waiting(self) -> list[Ticket]:
        # priority people first, then whoever came first
        return self._tickets(
            "status = ? ORDER BY priority DESC, joined_at ASC, id ASC", (WAITING,)
        )

    def serving(self) -> list[Ticket]:
        return self._tickets("status = ? ORDER BY counter", (SERVING,))

    def serving_at(self, counter: int) -> Optional[Ticket]:
        t = self._tickets("status = ? AND counter = ?", (SERVING, counter))
        return t[0] if t else None

    def find_active(self, phone: str) -> Optional[Ticket]:
        t = self._tickets("phone = ? AND status IN (?, ?)", (phone, *ACTIVE))
        return t[0] if t else None

    def position(self, ticket: Ticket) -> Optional[int]:
        # position in line (starts at 1), None if they're not waiting
        ids = [t.id for t in self.waiting()]
        return ids.index(ticket.id) + 1 if ticket.id in ids else None

    def avg_service_minutes(self, sample: int = 10) -> float:
        # avg time taken for the last 10 people
        # until 3 people are done we just use the number from settings
        rows = self.db.execute(
            "SELECT called_at, finished_at FROM tickets "
            "WHERE status = ? AND called_at IS NOT NULL AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT ?",
            (DONE, sample),
        ).fetchall()
        if len(rows) < 3:
            return float(self.get_setting("avg_minutes"))
        mins = [
            (datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["called_at"]))
            .total_seconds() / 60
            for r in rows
        ]
        return max(0.5, sum(mins) / len(mins))

    def wait_minutes(self, ticket: Ticket) -> int:
        # estimated wait in minutes
        # with c counters people get called c at a time, so we count how many
        # "rounds" are ahead of this person. free counters take people straight away
        pos = self.position(ticket)
        if pos is None:
            return 0
        c = self.counters
        free = c - len(self.serving())
        ahead = pos - 1 - free
        if ahead < 0:
            return 0
        rounds = ahead // c + 1
        return round(rounds * self.avg_service_minutes())

    # --- actions ---
    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _next_token(self) -> int:
        today = self.clock().date().isoformat()
        row = self.db.execute(
            "SELECT MAX(token) AS m FROM tickets WHERE substr(joined_at, 1, 10) = ?", (today,)
        ).fetchone()
        return (row["m"] or 0) + 1

    def join(self, phone_raw: str, name: str = "", priority: bool = False):
        # adds a person, returns (ticket, error)
        with self.lock:
            phone = clean_phone(phone_raw)
            if not phone:
                return None, "Enter a valid 10-digit Indian mobile number."
            if self.find_active(phone):
                return None, "This number is already in the queue."
            cur = self.db.execute(
                "INSERT INTO tickets(token, phone, name, priority, joined_at) VALUES (?, ?, ?, ?, ?)",
                (self._next_token(), phone, (name or "").strip() or "Guest", int(priority), self._now()),
            )
            self.db.commit()
            t = self.get(cur.lastrowid)
            self.notify(t, f"{self.business_name}: you're in the queue with token #{t.token}.")
            return t, None

    def _set(self, ticket_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE tickets SET {cols} WHERE id = ?", (*fields.values(), ticket_id))
        self.db.commit()

    def cancel(self, ticket_id: int) -> None:
        with self.lock:
            t = self.get(ticket_id)
            if t and t.status in ACTIVE:
                self._set(ticket_id, status=CANCELLED, finished_at=self._now())

    def complete(self, counter: int) -> Optional[Ticket]:
        # person at this counter is done
        with self.lock:
            t = self.serving_at(counter)
            if t:
                self._set(t.id, status=DONE, finished_at=self._now())
            return t

    def no_show(self, counter: int) -> Optional[Ticket]:
        # called but never came
        with self.lock:
            t = self.serving_at(counter)
            if t:
                self._set(t.id, status=NO_SHOW, finished_at=self._now())
            return t

    def call_next(self, counter: int = 1) -> Optional[Ticket]:
        # finish whoever is at the counter and call the next person
        with self.lock:
            self.complete(counter)
            w = self.waiting()
            if not w:
                return None
            t = w[0]
            self._set(t.id, status=SERVING, counter=counter, called_at=self._now())
            self.notify(t, f"{self.business_name}: token #{t.token}, it's your turn. "
                           f"Please go to counter {counter}.")
            self.process_reminders()  # the line moved, others may now need a reminder
            return self.get(t.id)

    def recall(self, counter: int) -> Optional[Ticket]:
        # send the your-turn message again
        t = self.serving_at(counter)
        if t:
            self.notify(t, f"{self.business_name}: reminder - token #{t.token}, "
                           f"please come to counter {counter} now.")
        return t

    def toggle_priority(self, ticket_id: int) -> None:
        with self.lock:
            t = self.get(ticket_id)
            if t and t.status == WAITING:
                self._set(ticket_id, priority=int(not t.priority))

    # --- sms ---
    def notify(self, ticket: Ticket, message: str) -> None:
        # not sending real sms yet, it just gets saved in the messages table
        # (would call twilio / msg91 here)
        self.db.execute(
            "INSERT INTO messages(sent_at, phone, body) VALUES (?, ?, ?)",
            (self._now(), ticket.phone, message),
        )
        self.db.commit()

    def outbox(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def process_reminders(self) -> int:
        # remind people who are close to their turn (only once each)
        sent = 0
        with self.lock:
            for t in self.waiting():
                if t.reminded:
                    continue
                wait = self.wait_minutes(t)
                if wait <= self.reminder_lead:
                    self._set(t.id, reminded=1)
                    self.notify(t, f"{self.business_name}: token #{t.token}, your turn is in "
                                   f"about {wait} min. Please head back now.")
                    sent += 1
        return sent

    # --- stats ---
    def history(self) -> list[Ticket]:
        return self._tickets("1=1 ORDER BY joined_at")

    def today_stats(self) -> dict:
        today = self.clock().date().isoformat()
        ts = self._tickets("substr(joined_at, 1, 10) = ?", (today,))
        called = [t for t in ts if t.called_at]
        done = [t for t in ts if t.status == DONE and t.finished_at and t.called_at]
        mins = lambda a, b: (b - a).total_seconds() / 60  # noqa: E731
        return {
            "joined": len(ts),
            "served": len(done),
            "waiting": sum(t.status == WAITING for t in ts),
            "no_shows": sum(t.status == NO_SHOW for t in ts),
            "cancelled": sum(t.status == CANCELLED for t in ts),
            "avg_wait": (sum(mins(t.joined_at, t.called_at) for t in called) / len(called)) if called else None,
            "avg_service": (sum(mins(t.called_at, t.finished_at) for t in done) / len(done)) if done else None,
        }

    # --- clearing / sample data ---
    def reset_all(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM tickets")
            self.db.execute("DELETE FROM messages")
            self.db.commit()

    SAMPLE_PEOPLE = [("9876543210", "Aarav"), ("9876543211", "Diya"), ("9876543212", "Kabir"),
                     ("9876543213", "Meera"), ("9876543214", "Rohan")]

    def load_sample_data(self) -> None:
        # 2 weeks of fake history + 5 people in the queue
        with self.lock:
            self.seed_demo_history()
            for phone, name in self.SAMPLE_PEOPLE:
                self.join(phone, name)
            self.set_setting("sample_loaded", 1)

    def ensure_sample_data(self) -> bool:
        # only on the very first start. if someone presses clear we leave it empty
        with self.lock:
            empty = self.db.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"] == 0
            if empty and self.get_setting("sample_loaded") != "1":
                self.load_sample_data()
                return True
            return False

    def seed_demo_history(self, days: int = 14, seed: int = 7) -> int:
        # makes up past visits so the charts have something to show
        # busier around 10-12 and 5-7pm, quieter on weekends
        rng = random.Random(seed)
        hourly_rate = {9: 4, 10: 9, 11: 11, 12: 7, 13: 3, 14: 4, 15: 5, 16: 6, 17: 9, 18: 8, 19: 3}
        today = self.clock().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = []
        with self.lock:
            for d in range(days, 0, -1):
                day = today - timedelta(days=d)
                weekend = day.weekday() >= 5
                token = 0
                for hour, rate in hourly_rate.items():
                    n = max(0, round(rng.gauss(rate * (0.6 if weekend else 1.0), 1.5)))
                    for _ in range(n):
                        token += 1
                        joined = day + timedelta(hours=hour, minutes=rng.uniform(0, 60))
                        wait = rng.uniform(2, 8 + rate * 2)
                        service = max(1.0, rng.gauss(5, 1.5))
                        called = joined + timedelta(minutes=wait)
                        status = rng.choices([DONE, NO_SHOW, CANCELLED], [90, 5, 5])[0]
                        finished = called + timedelta(minutes=service if status == DONE else 1)
                        rows.append((
                            token, f"9{rng.randint(100000000, 999999999)}", "Demo visitor",
                            int(rng.random() < 0.08), status, rng.randint(1, self.counters),
                            joined.isoformat(timespec="seconds"),
                            called.isoformat(timespec="seconds") if status != CANCELLED else None,
                            finished.isoformat(timespec="seconds"), 1,
                        ))
            self.db.executemany(
                "INSERT INTO tickets(token, phone, name, priority, status, counter, joined_at, "
                "called_at, finished_at, reminded) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.db.commit()
        return len(rows)
