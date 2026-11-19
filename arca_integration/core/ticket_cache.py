"""Ticket (TA) storage and freshness policy.

WSAA tickets last 12 hours but a request started a minute before expiry can
still fail, so a ticket is treated as spent a margin before its real expiry.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from .wsaa import Ticket

DEFAULT_MARGIN = timedelta(minutes=10)


class TicketCache(Protocol):
    def get(self, service: str) -> Ticket | None: ...

    def set(self, service: str, ticket: Ticket) -> None: ...


def is_valid(ticket: Ticket, now: datetime, margin: timedelta = DEFAULT_MARGIN) -> bool:
    if ticket.expires_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("ticket and now must be timezone-aware")
    return now < ticket.expires_at - margin


class MemoryTicketCache:
    """Process-local cache. Frappe wiring uses its own persistent implementation."""

    def __init__(self) -> None:
        self._tickets: dict[str, Ticket] = {}

    def get(self, service: str) -> Ticket | None:
        return self._tickets.get(service)

    def set(self, service: str, ticket: Ticket) -> None:
        self._tickets[service] = ticket


class FileTicketCache:
    """JSON-file cache.

    WSAA refuses a second login while a valid ticket exists
    (`coe.alreadyAuthenticated`), so the ticket must outlive the process.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, service: str) -> Ticket | None:
        entry = self._read().get(service)
        if not isinstance(entry, dict):
            return None
        try:
            return Ticket(
                token=entry["token"],
                sign=entry["sign"],
                expires_at=datetime.fromisoformat(entry["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def set(self, service: str, ticket: Ticket) -> None:
        data = self._read()
        data[service] = {
            "token": ticket.token,
            "sign": ticket.sign,
            "expires_at": ticket.expires_at.isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data))
        temporary.chmod(0o600)
        temporary.replace(self.path)
