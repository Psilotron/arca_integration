"""Numbering: the last authorized comprobante for a point of sale and type.

Fixtures in ``tests/fixtures/wsfe``, both captured from homologación on
2026-11-11:

``ultimo_autorizado.xml``
    ``PtoVta=1``, ``CbteTipo=6`` → ``CbteNro=0``. Zero means nothing was ever
    issued, which is a valid answer.
``ultimo_autorizado_error.xml``
    ``PtoVta=99999`` → ``Err 11000`` "no es un punto de venta valido".

A nonexistent point of sale also answers ``CbteNro=0`` without an error, so the
number alone never proves the point of sale exists; only ``FEParamGetPtosVenta``
does.
"""

from pathlib import Path

import pytest

from arca_integration.core.errors import WsfeRejected
from arca_integration.core.ticket_cache import MemoryTicketCache
from arca_integration.core.wsfe.client import WsfeClient
from arca_integration.core.wsfe.numbering import (
    build_last_authorized_request,
    next_voucher_number,
    parse_last_authorized,
)
from arca_integration.core.wsfe.xml import Auth
from arca_integration.core.wsaa import Ticket
from datetime import datetime, timezone

FIXTURES = Path(__file__).parent / "fixtures"
WSFE_FIXTURES = FIXTURES / "wsfe"
TICKET = Ticket("tok", "sig", datetime(2026, 11, 11, 23, 15, tzinfo=timezone.utc))


def test_parses_the_last_authorized_number():
    assert parse_last_authorized((WSFE_FIXTURES / "ultimo_autorizado.xml").read_bytes()) == 0


def test_last_authorized_zero_is_a_valid_answer():
    """Nothing issued yet is not an error: it is the starting point."""
    body = (WSFE_FIXTURES / "ultimo_autorizado.xml").read_bytes()

    assert parse_last_authorized(body) == 0


def test_rejection_names_the_error_code_and_reason():
    with pytest.raises(WsfeRejected) as excinfo:
        parse_last_authorized((WSFE_FIXTURES / "ultimo_autorizado_error.xml").read_bytes())

    assert excinfo.value.code == 11000
    assert "punto de venta" in excinfo.value.message.lower()
    assert "1 y 99998" in excinfo.value.detail


def test_next_number_is_last_plus_one():
    assert next_voucher_number(17) == 18
    assert next_voucher_number(0) == 1


def test_request_names_the_point_of_sale_and_the_type():
    request = build_last_authorized_request(Auth(TICKET, "20000000001"), 3, 6).decode()

    assert "<ar:PtoVta>3</ar:PtoVta>" in request
    assert "<ar:CbteTipo>6</ar:CbteTipo>" in request
    assert request.index("Auth") < request.index("<ar:PtoVta>")


def test_client_reads_the_last_authorized_number():
    calls = []
    bodies = []

    def http_post(url, payload, soap_action):
        calls.append(soap_action)
        if "LoginCms" in url:
            return (FIXTURES / "wsaa_response.xml").read_bytes()
        bodies.append(payload.decode())
        return (WSFE_FIXTURES / "ultimo_autorizado.xml").read_bytes()

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

    assert client.ultimo_autorizado(1, 6) == 0
    assert "http://ar.gov.afip.dif.FEV1/FECompUltimoAutorizado" in calls
    assert "<ar:PtoVta>1</ar:PtoVta>" in bodies[-1]
    assert "<ar:CbteTipo>6</ar:CbteTipo>" in bodies[-1]
