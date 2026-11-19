"""Requesting a CAE and classifying ARCA's four possible answers.

Fixtures in ``tests/fixtures/wsfe``:

``cae_rechazo_importes.xml``
    Captured from homologación on 2026-11-12. Real answer to a request whose
    ``ImpTotal`` did not match its components: ``Resultado: R`` with the detail
    in ``Observaciones.Obs``.
``cae_rechazo_cabecera.xml``
    Captured from homologación on 2026-11-12. Real answer to an impossible
    point of sale: ``Resultado: R`` with the code in ``Errors.Err``.
``cae_rechazo_duplicado.xml``
    Captured from homologación on 2026-11-12. Real answer to re-sending the
    number that had just been authorized: ``10016``.
``comprobante_autorizado.xml``
    Captured from homologación on 2026-11-12. Real ``FECompConsultar`` answer
    for the Factura B that *was* authorized (CAE 86461067947818).
``cae_aprobado.xml`` / ``cae_parcial.xml``
    CONSTRUCTED from the published WSDL and the real rejections above.
    ``cae_aprobado.xml`` is not the approval we actually got; see its header.

The two rejection shapes are different and both occur: a header-level failure
lands in ``Errors``, a detail-level failure lands in ``Observaciones``.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from arca_integration.core.errors import WsfeRejected, WsfeUncertain
from arca_integration.core.ticket_cache import MemoryTicketCache
from arca_integration.core.wsaa import Ticket
from arca_integration.core.wsfe.cae import (
    AlicIva,
    CaeRequest,
    build_cae_request,
    parse_cae_response,
)
from arca_integration.core.wsfe.client import WsfeClient
from arca_integration.core.wsfe.types import Cae, CondicionIvaReceptor
from arca_integration.core.wsfe.xml import Auth

FIXTURES = Path(__file__).parent / "fixtures"
WSFE_FIXTURES = FIXTURES / "wsfe"
TICKET = Ticket("tok", "sig", datetime(2026, 11, 12, 23, 15, tzinfo=timezone.utc))


def _request(**overrides):
    values = dict(
        pto_venta=1,
        cbte_tipo=6,
        cbte_desde=1,
        cbte_fch="20261112",
        doc_tipo=99,
        doc_nro=0,
        imp_total=121.0,
        imp_neto=100.0,
        imp_iva=21.0,
        condicion_iva_receptor=CondicionIvaReceptor.CONSUMIDOR_FINAL,
        alicuotas=[AlicIva(5, 100.0, 21.0)],
    )
    values.update(overrides)
    return CaeRequest(**values)


def test_parses_an_approved_cae():
    cae = parse_cae_response((WSFE_FIXTURES / "cae_aprobado.xml").read_bytes())

    assert isinstance(cae, Cae)
    assert cae.resultado == "A"
    assert cae.cae.isdigit()
    assert len(cae.vence) == 8
    assert cae.reproceso == "N"


def test_rejection_reports_the_observation_code_and_message():
    """A detail-level rejection carries its reason in Observaciones, not Errors."""
    with pytest.raises(WsfeRejected) as excinfo:
        parse_cae_response((WSFE_FIXTURES / "cae_rechazo_importes.xml").read_bytes())

    assert excinfo.value.code == 10048
    assert "Importe Total" in excinfo.value.message


def test_rejection_reads_the_top_level_errors():
    """A header-level rejection carries its reason in Errors."""
    with pytest.raises(WsfeRejected) as excinfo:
        parse_cae_response((WSFE_FIXTURES / "cae_rechazo_cabecera.xml").read_bytes())

    assert excinfo.value.code == 10004
    assert "PtoVta" in excinfo.value.message


def test_a_duplicate_number_is_rejected():
    """Re-sending an authorized comprobante's number is not a new issuance."""
    with pytest.raises(WsfeRejected) as excinfo:
        parse_cae_response((WSFE_FIXTURES / "cae_rechazo_duplicado.xml").read_bytes())

    assert excinfo.value.code == 10016
    assert "FECompUltimoAutorizado" in excinfo.value.message


def test_partial_answer_is_uncertain():
    """``Resultado: P`` means the comprobante may or may not exist: never guess."""
    with pytest.raises(WsfeUncertain):
        parse_cae_response((WSFE_FIXTURES / "cae_parcial.xml").read_bytes())


def test_a_rejection_that_carries_a_cae_still_raises():
    body = (
        b'<FECAESolicitarResponse xmlns="http://ar.gov.afip.dif.FEV1/">'
        b"<FECAESolicitarResult><FeCabResp><Resultado>R</Resultado></FeCabResp>"
        b"<FeDetResp><FECAEDetResponse><Resultado>R</Resultado>"
        b"<CAE>86390929393429</CAE><CAEFchVto>20261122</CAEFchVto>"
        b"</FECAEDetResponse></FeDetResp></FECAESolicitarResult>"
        b"</FECAESolicitarResponse>"
    )

    with pytest.raises(WsfeRejected):
        parse_cae_response(body)


def test_an_unknown_resultado_is_uncertain():
    body = (
        b'<FECAESolicitarResponse xmlns="http://ar.gov.afip.dif.FEV1/">'
        b"<FECAESolicitarResult><FeCabResp><Resultado>X</Resultado></FeCabResp>"
        b"</FECAESolicitarResult></FECAESolicitarResponse>"
    )

    with pytest.raises(WsfeUncertain):
        parse_cae_response(body)


def test_request_puts_auth_first_and_one_register():
    request = build_cae_request(
        Auth(TICKET, "20000000001"), _request()
    ).decode()

    assert "<ar:CantReg>1</ar:CantReg>" in request
    assert "<ar:PtoVta>1</ar:PtoVta>" in request
    assert "<ar:CbteTipo>6</ar:CbteTipo>" in request
    assert request.index("Auth") < request.index("<ar:FeCAEReq>")
    assert request.index("<ar:FeCabReq>") < request.index("<ar:FeDetReq>")


def test_request_discriminates_the_iva_and_the_receiver_condition():
    request = build_cae_request(
        Auth(TICKET, "20000000001"), _request()
    ).decode()

    assert "<ar:CondicionIVAReceptorId>5</ar:CondicionIVAReceptorId>" in request
    assert "<ar:Iva><ar:AlicIva><ar:Id>5</ar:Id>" in request
    assert "<ar:BaseImp>100.00</ar:BaseImp>" in request
    assert "<ar:Importe>21.00</ar:Importe>" in request


def test_request_amounts_carry_two_decimals():
    request = build_cae_request(
        Auth(TICKET, "20000000001"), _request(imp_total=121.0, imp_neto=100.0)
    ).decode()

    assert "<ar:ImpTotal>121.00</ar:ImpTotal>" in request
    assert "<ar:ImpNeto>100.00</ar:ImpNeto>" in request


def test_request_omits_the_service_dates_for_a_product():
    """Concepto 1 is a product: the service dates and due date must not appear."""
    request = build_cae_request(
        Auth(TICKET, "20000000001"), _request()
    ).decode()

    assert "FchServDesde" not in request
    assert "FchServHasta" not in request
    assert "FchVtoPago" not in request


def test_request_starts_and_ends_with_an_authorized_range():
    request = build_cae_request(
        Auth(TICKET, "20000000001"), _request(cbte_desde=17)
    ).decode()

    assert "<ar:CbteDesde>17</ar:CbteDesde>" in request
    assert "<ar:CbteHasta>17</ar:CbteHasta>" in request


def test_client_requests_a_cae():
    calls = []
    bodies = []

    def http_post(url, payload, soap_action):
        calls.append(soap_action)
        if "LoginCms" in url:
            return (FIXTURES / "wsaa_response.xml").read_bytes()
        bodies.append(payload.decode())
        return (WSFE_FIXTURES / "cae_aprobado.xml").read_bytes()

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

    cae = client.solicitar_cae(_request())

    assert cae.cae == "86390929393429"
    assert "http://ar.gov.afip.dif.FEV1/FECAESolicitar" in calls
    assert "<ar:FeCAEReq>" in bodies[-1]
