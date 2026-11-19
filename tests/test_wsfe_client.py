from datetime import datetime, timezone
from pathlib import Path

from arca_integration.core.ticket_cache import MemoryTicketCache
from arca_integration.core.wsfe.client import WSFE_SERVICE, WsfeClient

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def make_client(calls, cache=None):
    def http_post(url, body, soap_action):
        calls.append((url, soap_action))
        if "LoginCms" in url:
            return (FIXTURES / "wsaa_response.xml").read_bytes()
        return b"<ok/>"

    return WsfeClient(
        wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        wsfe_url="https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
        cert_path="unused.crt",
        key_path="unused.key",
        cuit_representada="20000000001",
        cache=cache or MemoryTicketCache(),
        http_post=http_post,
        signer=lambda xml: b"cms",
    )


def logins(calls):
    return sum(1 for url, _ in calls if "LoginCms" in url)


def test_ticket_is_cached_under_the_wsfe_service():
    calls = []
    cache = MemoryTicketCache()
    client = make_client(calls, cache)

    client.ticket(NOW)
    client.ticket(NOW)

    assert logins(calls) == 1
    assert cache.get(WSFE_SERVICE) is not None
    assert cache.get("ws_sr_constancia_inscripcion") is None


def test_call_forwards_the_operation_as_soap_action():
    calls = []
    client = make_client(calls)

    client.call("urn:FECAESolicitar", b"<body/>")

    assert calls[-1] == (
        "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
        "urn:FECAESolicitar",
    )
