from datetime import datetime, timezone
from pathlib import Path

import pytest

from arca_integration.core.client import ArcaClient
from arca_integration.core.errors import ConstanciaError, PersonaNotFound, TransportError
from arca_integration.core.ticket_cache import MemoryTicketCache

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 12, 2, 10, tzinfo=timezone.utc)  # fixture TA expires 02:15Z


def fake_post(calls, padron_fixture="persona_juridica.xml"):
    def post(url: str, body: bytes) -> bytes:
        calls.append((url, body))
        if "LoginCms" in url:
            return (FIXTURES / "wsaa_response.xml").read_bytes()
        return (FIXTURES / padron_fixture).read_bytes()

    return post


def make_client(http_post=None, cache=None, post_impl=None):
    return ArcaClient(
        wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        padron_url="https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
        cert_path="unused.crt",
        key_path="unused.key",
        cuit_representada="20000000001",
        cache=cache,
        http_post=http_post,
        signer=lambda xml: b"cms",
    )


def logins(calls):
    return sum(1 for url, _ in calls if "LoginCms" in url)


def test_lookup_reuses_cached_ticket():
    calls = []
    client = make_client(http_post=fake_post(calls), cache=MemoryTicketCache())

    client.lookup("30500001735", now=NOW)
    client.lookup("30500001735", now=NOW)

    assert logins(calls) == 1


def test_lookup_refreshes_expired_ticket():
    calls = []
    client = make_client(http_post=fake_post(calls), cache=MemoryTicketCache())

    client.lookup("30500001735", now=NOW)
    client.lookup("30500001735", now=LATER)

    assert logins(calls) == 2


def test_lookup_normalizes_cuit_before_sending():
    calls = []
    client = make_client(http_post=fake_post(calls), cache=MemoryTicketCache())

    client.lookup("30-50000173-5", now=NOW)

    assert b"<idPersona>30500001735</idPersona>" in calls[-1][1]


def test_lookup_returns_persona():
    client = make_client(http_post=fake_post([]), cache=MemoryTicketCache())

    persona = client.lookup("30500001735", now=NOW)

    assert persona.razon_social == "BANCO PRUEBA 02"


def test_lookup_maps_constancia_error():
    client = make_client(
        http_post=fake_post([], "persona_error.xml"), cache=MemoryTicketCache()
    )

    with pytest.raises(ConstanciaError) as excinfo:
        client.lookup("20222222223", now=NOW)

    assert "biométricos" in excinfo.value.message


def test_lookup_maps_fault():
    client = make_client(
        http_post=fake_post([], "persona_fault.xml"), cache=MemoryTicketCache()
    )

    with pytest.raises(PersonaNotFound):
        client.lookup("20000000001", now=NOW)


def test_lookup_maps_transport_failure():
    def boom(url, body):
        raise ConnectionError("network down")

    client = make_client(http_post=boom, cache=MemoryTicketCache())

    with pytest.raises(TransportError):
        client.lookup("30500001735", now=NOW)


def test_bad_cuit_fails_before_any_network_call():
    calls = []
    client = make_client(http_post=fake_post(calls), cache=MemoryTicketCache())

    with pytest.raises(ValueError):
        client.lookup("123", now=NOW)

    assert calls == []


def test_default_post_returns_fault_body_on_http_500(monkeypatch):
    from arca_integration.core.client import _default_post

    class Response:
        status_code = 500
        text = "<soap:Fault><faultstring>No existe persona con ese Id</faultstring></soap:Fault>"
        content = text.encode()

    monkeypatch.setattr("requests.post", lambda *a, **k: Response())

    assert b"faultstring" in _default_post("https://example.test", b"body")


def test_default_post_raises_on_http_500_without_fault(monkeypatch):
    from arca_integration.core.client import _default_post

    class Response:
        status_code = 502
        text = "Bad gateway"
        content = text.encode()

    monkeypatch.setattr("requests.post", lambda *a, **k: Response())

    with pytest.raises(TransportError):
        _default_post("https://example.test", b"body")


def test_default_post_honours_explicit_timeout(monkeypatch):
    from arca_integration.core.client import _default_post

    seen = {}

    class Response:
        status_code = 200
        text = ""
        content = b""

    def fake_post(url, data=None, headers=None, timeout=None):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("requests.post", fake_post)

    _default_post("https://example.test", b"body", timeout=5)

    assert seen["timeout"] == 5


def test_client_binds_its_timeout_to_the_default_transport(monkeypatch):
    import arca_integration.core.client as client_module

    seen = {}

    def fake_default_post(url, body, timeout=30):
        seen["timeout"] = timeout
        return b""

    monkeypatch.setattr(client_module, "_default_post", fake_default_post)
    client = ArcaClient(
        wsaa_url="u",
        padron_url="p",
        cert_path="c",
        key_path="k",
        cuit_representada="20000000001",
        timeout=9,
    )

    client._call("u", b"body")

    assert seen["timeout"] == 9


# --- TLS: how the CA bundle is chosen -------------------------------------


class _Response:
    status_code = 200
    text = ""
    content = b""


def _capturar_post(monkeypatch):
    """Replace ``requests.post`` with one that records its keyword arguments."""
    seen = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        seen.update(kwargs)
        return _Response()

    monkeypatch.setattr("requests.post", fake_post)
    return seen


def test_default_post_leaves_verification_at_the_requests_default(monkeypatch):
    """No bundle configured: keep certifi, do not pass ``verify`` at all."""
    from arca_integration.core.client import _default_post

    seen = _capturar_post(monkeypatch)
    monkeypatch.delenv("ARCA_CA_BUNDLE", raising=False)

    _default_post("https://example.test", b"body")

    assert "verify" not in seen


def test_default_post_uses_the_configured_ca_bundle(monkeypatch, tmp_path):
    from arca_integration.core.client import _default_post

    bundle = tmp_path / "arca-ca-bundle.pem"
    bundle.write_text("")

    seen = _capturar_post(monkeypatch)
    monkeypatch.setenv("ARCA_CA_BUNDLE", str(bundle))

    _default_post("https://example.test", b"body")

    assert seen["verify"] == str(bundle)


def test_default_post_never_disables_verification(monkeypatch, tmp_path):
    """``verify=False`` would turn any intercepting proxy into an ARCA answer."""
    from arca_integration.core.client import _default_post

    bundle = tmp_path / "arca-ca-bundle.pem"
    bundle.write_text("")

    sin_variable = _capturar_post(monkeypatch)
    monkeypatch.delenv("ARCA_CA_BUNDLE", raising=False)
    _default_post("https://example.test", b"body")

    con_variable = _capturar_post(monkeypatch)
    monkeypatch.setenv("ARCA_CA_BUNDLE", str(bundle))
    _default_post("https://example.test", b"body")

    assert sin_variable.get("verify") is not False
    assert con_variable.get("verify") is not False


def test_a_bundle_path_that_does_not_exist_is_refused(monkeypatch):
    """requests would raise an OSError outside our error taxonomy."""
    from arca_integration.core.client import _default_post

    monkeypatch.setenv("ARCA_CA_BUNDLE", "/nonexistent/arca-ca-bundle.pem")

    with pytest.raises(TransportError) as excinfo:
        _default_post("https://example.test", b"body")

    assert "ARCA_CA_BUNDLE" in excinfo.value.message
    assert "/nonexistent/arca-ca-bundle.pem" in excinfo.value.detail


def test_a_verification_failure_is_reported_as_a_transport_error(monkeypatch):
    """The OpenSSL reason must survive into ``detail``; it is the diagnosis."""
    import requests as requests_module

    from arca_integration.core.client import _default_post

    def fake_post(*args, **kwargs):
        raise requests_module.exceptions.SSLError(
            "[SSL: UNABLE_TO_GET_ISSUER_CERT_LOCALLY] unable to get local issuer "
            "certificate (_ssl.c:1056)"
        )

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.delenv("ARCA_CA_BUNDLE", raising=False)

    with pytest.raises(TransportError) as excinfo:
        _default_post("https://example.test", b"body")

    assert excinfo.value.message == "No se pudo conectar con ARCA."
    assert "unable to get local issuer certificate" in excinfo.value.detail
