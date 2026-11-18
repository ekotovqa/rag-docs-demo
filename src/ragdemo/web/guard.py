"""Abuse and spend guard for a public demo.

The demo is advertised openly, so a visitor gets a few questions to see how it works, not an
open endpoint. Four layers, cheapest first:

- per visitor (the client IP): a handful of questions per day and a few per minute;
- for everyone together: questions per minute and questions in flight, so one burst cannot
  take the single replica down or drain the budget in seconds;
- money: a daily and a monthly ceiling in USD. The totals live in Postgres, because the app
  scales to zero and an in-memory counter would reset on every cold start;
- request size: a question is 300 characters, so a body over a few KB is refused before parsing.

Everything is counted in UTC. Refusals carry a Retry-After header where waiting helps.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from fastapi import HTTPException, Request

log = logging.getLogger("ragdemo.guard")

MAX_TRACKED_VISITORS = 200_000  # bounded memory under a flood of addresses


@dataclass(frozen=True)
class Limits:
    per_minute: int = 5          # questions per minute, one visitor
    per_visitor_day: int = 5     # questions per day, one visitor
    global_per_minute: int = 30  # questions per minute, everyone
    max_inflight: int = 2        # questions being answered at once
    daily_usd: float = 2.0
    monthly_usd: float = 15.0


def limits_from_env() -> Limits:
    e = os.environ.get
    return Limits(
        per_minute=int(e("RAGDEMO_RATE_LIMIT_PER_MINUTE", "5")),
        per_visitor_day=int(e("RAGDEMO_VISITOR_DAILY_QUESTIONS", "5")),
        global_per_minute=int(e("RAGDEMO_GLOBAL_PER_MINUTE", "30")),
        max_inflight=int(e("RAGDEMO_MAX_INFLIGHT", "2")),
        daily_usd=float(e("RAGDEMO_DAILY_BUDGET_USD", "2.0")),
        monthly_usd=float(e("RAGDEMO_MONTHLY_BUDGET_USD", "15")),
    )


class Ledger(Protocol):
    def spend_load(self, day: str) -> tuple[float, float]:
        """(spent on `day`, spent in the month of `day`), USD."""

    def spend_add(self, day: str, usd: float) -> None: ...


class MemoryLedger:
    """For tests and for the time before the database is attached."""

    def __init__(self) -> None:
        self.rows: dict[str, float] = {}

    def spend_load(self, day: str) -> tuple[float, float]:
        return self.rows.get(day, 0.0), sum(v for d, v in self.rows.items() if d[:7] == day[:7])

    def spend_add(self, day: str, usd: float) -> None:
        self.rows[day] = self.rows.get(day, 0.0) + usd


class Refused(HTTPException):
    def __init__(self, status: int, reason: str, detail: str, retry_after: int | None = None) -> None:
        super().__init__(status, detail, {"Retry-After": str(retry_after)} if retry_after else None)
        self.reason = reason


class Guard:
    def __init__(self, limits: Limits, ledger: Ledger | None = None, clock: Callable[[], float] = time.time) -> None:
        self.limits = limits
        self.ledger: Ledger = ledger or MemoryLedger()
        self.clock = clock
        self.lock = threading.Lock()
        self.day = ""
        self.today_usd = 0.0
        self.month_usd = 0.0
        self.visitor_minute: dict[str, deque[float]] = defaultdict(deque)
        self.visitor_day: dict[str, int] = defaultdict(int)
        self.global_minute: deque[float] = deque()
        self.inflight = 0

    def attach(self, ledger: Ledger) -> None:
        """Switch to the durable ledger (the store) once it is up, and read today's totals from it."""
        with self.lock:
            self.ledger = ledger
            self.day = ""
            self._roll()

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if day == self.day:
            return
        try:
            self.today_usd, self.month_usd = self.ledger.spend_load(day)
        except Exception:
            # Unreadable ledger: keep what we have rather than start from zero. Over-counting is the safe side.
            log.exception("guard: ledger unreadable, keeping in-memory totals")
            if day[:7] != self.day[:7]:
                self.month_usd = 0.0
            self.today_usd = 0.0
        self.day = day
        self.visitor_day.clear()
        self.visitor_minute.clear()

    def admit(self, ip: str) -> None:
        """Reserve a slot for one question or raise `Refused`. Every admitted question must be `release`d."""
        with self.lock:
            self._roll()
            now = self.clock()
            lim = self.limits
            if self.month_usd >= lim.monthly_usd:
                self._refuse(ip, 429, "monthly_budget", "The demo's monthly budget is used up; it is back next month.")
            if self.today_usd >= lim.daily_usd:
                self._refuse(ip, 429, "daily_budget", "The demo's daily budget is used up; try again tomorrow (UTC).")
            if ip not in self.visitor_day and len(self.visitor_day) >= MAX_TRACKED_VISITORS:
                self._refuse(ip, 503, "flood", "The demo is under unusual load; try again later.", 300)
            if self.visitor_day[ip] >= lim.per_visitor_day:
                self._refuse(ip, 429, "visitor_day",
                             f"This demo gives each visitor {lim.per_visitor_day} questions a day, and you have used "
                             f"them. Tomorrow (UTC) brings {lim.per_visitor_day} more; for a longer session, get in touch.")
            recent = self.visitor_minute[ip]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= lim.per_minute:
                self._refuse(ip, 429, "visitor_minute", f"At most {lim.per_minute} questions per minute, please.", 60)
            while self.global_minute and now - self.global_minute[0] > 60:
                self.global_minute.popleft()
            if len(self.global_minute) >= lim.global_per_minute:
                self._refuse(ip, 429, "busy_minute", "The demo is busy right now; try again in a minute.", 60)
            if self.inflight >= lim.max_inflight:
                self._refuse(ip, 503, "busy", "The demo is answering other questions right now; try again in a few seconds.", 5)
            recent.append(now)
            self.global_minute.append(now)
            self.visitor_day[ip] += 1
            self.inflight += 1

    def release(self, cost: float | None) -> None:
        with self.lock:
            self.inflight = max(0, self.inflight - 1)
            if not cost:
                return
            self.today_usd += cost
            self.month_usd += cost
            day = self.day
        try:
            self.ledger.spend_add(day, cost)
        except Exception:
            log.exception("guard: could not persist spend; the in-memory total still counts it")

    def left_today(self, ip: str) -> int:
        with self.lock:
            return max(0, self.limits.per_visitor_day - self.visitor_day.get(ip, 0))

    def _refuse(self, ip: str, status: int, reason: str, detail: str, retry_after: int | None = None) -> None:
        log.warning("guard refused ip=%s reason=%s today=%.3f month=%.3f", ip, reason, self.today_usd, self.month_usd)
        raise Refused(status, reason, detail, retry_after)


def client_ip(request: Request) -> str:
    """The address the ingress saw. Container Apps' Envoy appends it as the LAST entry of
    X-Forwarded-For, after anything the client sent itself, so the last entry is the trusted one.
    (Behind a CDN this would be the CDN's edge: trust one more hop then.)"""
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if parts:
        return parts[-1]
    return request.client.host if request.client else "?"


class BodyLimit:
    """Refuse request bodies above `max_bytes` by Content-Length, before anything reads them."""

    def __init__(self, app: Any, max_bytes: int = 4096) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("method") in ("POST", "PUT", "PATCH"):
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            length = headers.get(b"content-length")
            status, text = 0, ""
            if length is None:
                status, text = 411, "Content-Length required"
            elif not length.isdigit() or int(length) > self.max_bytes:
                status, text = 413, f"Request body above {self.max_bytes} bytes"
            if status:
                body = ('{"detail": "%s"}' % text).encode()
                await send({"type": "http.response.start", "status": status,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
