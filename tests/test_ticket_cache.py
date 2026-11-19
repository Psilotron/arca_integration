from datetime import datetime, timedelta, timezone

import pytest

from arca_integration.core.ticket_cache import MemoryTicketCache, is_valid
from arca_integration.core.wsaa import Ticket


def test_ticket_invalid_inside_margin():
    ticket = Ticket("tok", "sig", datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))

    assert is_valid(ticket, datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc))
    assert not is_valid(ticket, datetime(2026, 11, 11, 11, 55, tzinfo=timezone.utc))


def test_ticket_invalid_after_expiry():
    ticket = Ticket("tok", "sig", datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))

    assert not is_valid(ticket, datetime(2026, 11, 11, 12, 1, tzinfo=timezone.utc))


def test_naive_datetime_is_rejected():
    ticket = Ticket("tok", "sig", datetime(2026, 11, 11, 12, 0))

    with pytest.raises(ValueError):
        is_valid(ticket, datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc))


def test_memory_cache_roundtrip_per_service():
    cache = MemoryTicketCache()
    ticket = Ticket("tok", "sig", datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))

    cache.set("ws_sr_constancia_inscripcion", ticket)

    assert cache.get("ws_sr_constancia_inscripcion") is ticket
    assert cache.get("ws_sr_padron_a5") is None


def test_margin_is_configurable():
    ticket = Ticket("tok", "sig", datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))

    assert is_valid(
        ticket,
        datetime(2026, 11, 11, 11, 55, tzinfo=timezone.utc),
        margin=timedelta(minutes=1),
    )


def test_file_cache_roundtrip(tmp_path):
    from arca_integration.core.ticket_cache import FileTicketCache

    cache = FileTicketCache(tmp_path / "ta.json")
    ticket = Ticket("tok", "sig", datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))

    cache.set("ws_sr_constancia_inscripcion", ticket)

    assert cache.get("ws_sr_constancia_inscripcion") == ticket
    assert cache.get("other") is None


def test_file_cache_survives_corrupt_file(tmp_path):
    from arca_integration.core.ticket_cache import FileTicketCache

    path = tmp_path / "ta.json"
    path.write_text("{not json")

    assert FileTicketCache(path).get("ws_sr_constancia_inscripcion") is None


def test_file_cache_handles_missing_file(tmp_path):
    from arca_integration.core.ticket_cache import FileTicketCache

    assert FileTicketCache(tmp_path / "absent.json").get("x") is None
