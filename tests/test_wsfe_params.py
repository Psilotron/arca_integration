"""Contract tests for the WSFE parameter catalogs.

Fixtures in ``tests/fixtures/wsfe``:

``tipos_cbte.xml``
    Captured from homologación on 2026-11-11. Real answer, 36 comprobante types.
``ptos_venta_vacio.xml``
    Captured from homologación on 2026-11-11. Real answer: ``Err 602``.
    It is not proof that no point of sale exists: this CUIT answered 602 and
    still issued from point of sale 1 the next day.
``ptos_venta.xml``
    CONSTRUCTED from the published WSDL shape. It is not evidence: no real
    non-empty answer could be captured yet.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from arca_integration.core.errors import WsfeError
from arca_integration.core.ticket_cache import MemoryTicketCache
from arca_integration.core.wsaa import Ticket
from arca_integration.core.wsfe.client import WsfeClient
from arca_integration.core.wsfe.params import (
    build_param_request,
    parse_ptos_venta,
    parse_tipos_cbte,
)
from arca_integration.core.wsfe.types import ComprobanteType, PuntoVenta
from arca_integration.core.wsfe.xml import Auth, auth_xml, build_envelope

FIXTURES = Path(__file__).parent / "fixtures"
WSFE_FIXTURES = FIXTURES / "wsfe"
TICKET = Ticket("tok", "sig", datetime(2026, 11, 11, 23, 15, tzinfo=timezone.utc))


def test_auth_xml_carries_token_sign_and_cuit():
    body = auth_xml(Auth(TICKET, "20000000001"))

    assert "<ar:Token>tok</ar:Token>" in body
    assert "<ar:Sign>sig</ar:Sign>" in body
    assert "<ar:Cuit>20000000001</ar:Cuit>" in body


def test_envelope_names_the_operation_and_declares_the_namespace():
    envelope = build_envelope("FEParamGetPtosVenta", "<ar:Auth/>").decode()

    assert envelope.startswith("<?xml")
    assert 'xmlns:ar="http://ar.gov.afip.dif.FEV1/"' in envelope
    assert "<ar:FEParamGetPtosVenta><ar:Auth/></ar:FEParamGetPtosVenta>" in envelope


def test_auth_comes_before_the_rest_of_the_body():
    """SOAP 1.1 document/literal is order sensitive; Auth is always first."""
    sent = []

    def http_post(url, payload, soap_action):
        if "LoginCms" in url:
            return (FIXTURES / "wsaa_response.xml").read_bytes()
        sent.append(payload.decode() if isinstance(payload, bytes) else payload)
        return b"<ok/>"

    client = WsfeClient(
        wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        wsfe_url="https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
        cert_path="unused.crt",
        key_path="unused.key",
        cuit_representada="20000000001",
        cache=MemoryTicketCache(),
        http_post=http_post,
        signer=lambda xml: b"cms",
    )

    client._operation("OperacionDePrueba", "<ar:Dato/>")

    assert sent[-1].index("Auth") < sent[-1].index("<ar:Dato/>")


def test_parses_tipos_de_comprobante():
    tipos = parse_tipos_cbte((WSFE_FIXTURES / "tipos_cbte.xml").read_bytes())

    assert len(tipos) == 36
    assert all(isinstance(t, ComprobanteType) for t in tipos)
    factura_a = next(t for t in tipos if t.id == 1)
    assert factura_a.desc == "Factura A"
    assert factura_a.fch_desde == "20100917"
    assert factura_a.fch_hasta is None


def test_parses_puntos_de_venta():
    ptos = parse_ptos_venta((WSFE_FIXTURES / "ptos_venta.xml").read_bytes())

    assert [p.nro for p in ptos] == [1, 2, 3]
    assert all(isinstance(p, PuntoVenta) for p in ptos)
    assert ptos[0].emision_tipo == "CAE"
    assert ptos[0].bloqueado is False
    assert ptos[0].fch_baja is None
    assert ptos[2].bloqueado is True
    assert ptos[2].fch_baja == "20240101"


def test_an_empty_point_of_sale_catalog_raises_instead_of_returning_a_list():
    """602 is an error body, not an empty catalog: never render it as []."""
    with pytest.raises(WsfeError) as excinfo:
        parse_ptos_venta((WSFE_FIXTURES / "ptos_venta_vacio.xml").read_bytes())

    assert excinfo.value.code == 602
    assert "puntos de venta" in excinfo.value.message.lower()
    assert "FEParamGetPtosVenta" in excinfo.value.detail


def test_the_point_of_sale_catalog_is_not_a_health_check():
    """This CUIT answered 602 and still got a comprobante authorized.

    Pinned so nobody reintroduces a preflight that blocks issuance on this
    catalog: the empty answer says nothing about whether issuing works.
    """
    with pytest.raises(WsfeError):
        parse_ptos_venta((WSFE_FIXTURES / "ptos_venta_vacio.xml").read_bytes())

    authorized = (WSFE_FIXTURES / "comprobante_autorizado.xml").read_text()
    assert "<Resultado>A</Resultado>" in authorized
    assert "<CodAutorizacion>86461067947818</CodAutorizacion>" in authorized


def _client(calls, body):
    def http_post(url, payload, soap_action):
        calls.append(soap_action)
        if "LoginCms" in url:
            return (FIXTURES / "wsaa_response.xml").read_bytes()
        return body

    return WsfeClient(
        wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        wsfe_url="https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
        cert_path="unused.crt",
        key_path="unused.key",
        cuit_representada="20000000001",
        cache=MemoryTicketCache(),
        http_post=http_post,
        signer=lambda xml: b"cms",
    )


def test_client_returns_puntos_de_venta():
    calls = []
    client = _client(calls, (WSFE_FIXTURES / "ptos_venta.xml").read_bytes())

    assert [p.nro for p in client.ptos_venta()] == [1, 2, 3]
    assert "http://ar.gov.afip.dif.FEV1/FEParamGetPtosVenta" in calls


def test_client_returns_tipos_de_comprobante():
    calls = []
    client = _client(calls, (WSFE_FIXTURES / "tipos_cbte.xml").read_bytes())

    assert len(client.tipos_cbte()) == 36
    assert "http://ar.gov.afip.dif.FEV1/FEParamGetTiposCbte" in calls


def test_param_request_carries_the_auth_block():
    request = build_param_request(
        "FEParamGetTiposCbte", Auth(TICKET, "20000000001")
    ).decode()

    assert request.startswith("<?xml")
    assert "<ar:FEParamGetTiposCbte>" in request
    assert "<ar:Token>tok</ar:Token>" in request
    assert "<ar:Cuit>20000000001</ar:Cuit>" in request
