import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arca_integration.core.ticket_cache import FileTicketCache
from arca_integration.core.wsfe import AlicIva, CaeRequest, CondicionIvaReceptor
from arca_integration.core.wsfe.client import WsfeClient
from arca_integration.core.wsfe.numbering import next_voucher_number
from arca_integration.core.errors import WsfeError, WsfeRejected, WsfeUncertain

pytestmark = pytest.mark.live

LIVE = os.getenv("ARCA_LIVE") == "1"
# Issuing is irreversible, so it needs its own switch: ARCA_LIVE only means the
# suite may reach the network, never that it may mint a comprobante.
EMITIR = os.getenv("ARCA_LIVE_EMITIR") == "1"
CUIT = os.environ.get("ARCA_CUIT")
TA_CACHE = os.environ.get("ARCA_WSFE_TA_CACHE", "/tmp/arca-wsfe-ta.json")


@pytest.fixture
def client():
    if not LIVE:
        pytest.skip("set ARCA_LIVE=1 to run against homologación")
    if not CUIT:
        pytest.skip("set ARCA_CUIT to the CUIT the certificate was issued to")
    return WsfeClient(
        wsaa_url=os.environ.get(
            "ARCA_WSAA_URL", "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
        ),
        wsfe_url=os.environ.get(
            "ARCA_WSFE_URL", "https://wswhomo.afip.gov.ar/wsfev1/service.asmx"
        ),
        cert_path=os.environ.get("ARCA_CERT", "tmp/erp.crt"),
        key_path=os.environ.get("ARCA_KEY", "tmp/erp.pem"),
        cuit_representada=CUIT,
        cache=FileTicketCache(TA_CACHE),
    )


FEDUMMY_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ar="http://ar.gov.afip.dif.FEV1/">'
    "<soapenv:Header/><soapenv:Body><ar:FEDummy/></soapenv:Body></soapenv:Envelope>"
).encode()


def test_live_fedummy_needs_no_ticket(client):
    """Health check: proves the endpoint, the envelope and the SOAPAction.

    FEDummy requires no Auth, so this never consumes the 12-hour ticket slot.
    """
    answer = client.call("http://ar.gov.afip.dif.FEV1/FEDummy", FEDUMMY_ENVELOPE).decode()

    assert "<AppServer>OK</AppServer>" in answer
    assert "<DbServer>OK</DbServer>" in answer
    assert "<AuthServer>OK</AuthServer>" in answer


def test_live_wsfe_ticket_is_obtained_and_reused(client):
    first = client.ticket()
    second = client.ticket()

    assert first.token
    assert second == first


def test_live_ptos_venta_is_either_a_catalog_or_the_documented_602(client):
    """A CUIT without a point of sale answers Err 602, never an empty list.

    Both states are legitimate. What must never happen is a silent ``[]``.
    """
    try:
        ptos = client.ptos_venta()
    except WsfeError as exc:
        assert exc.code == 602
        assert "puntos de venta" in exc.message.lower()
    else:
        assert ptos
        assert all(isinstance(p.nro, int) for p in ptos)


def test_live_tipos_de_comprobante_include_factura_a_and_b(client):
    tipos = client.tipos_cbte()
    descs = {t.id: t.desc for t in tipos}

    assert "Factura A" in descs[1]
    assert "Factura B" in descs[6]


def test_live_ultimo_autorizado_answers_a_number(client):
    assert client.ultimo_autorizado(1, 6) >= 0


def test_live_ultimo_autorizado_rejects_an_impossible_point_of_sale(client):
    with pytest.raises(WsfeRejected) as excinfo:
        client.ultimo_autorizado(99999, 6)

    assert excinfo.value.code == 11000
    assert "punto de venta" in excinfo.value.message.lower()


def test_live_consultar_reads_an_authorized_comprobante_back(client):
    """The only exit from an uncertain issuance: read what ARCA recorded."""
    ultimo = client.ultimo_autorizado(1, 6)
    if ultimo == 0:
        pytest.skip("nothing authorized from point of sale 1 yet")

    consulta = client.consultar_comprobante(1, 6, ultimo)

    assert consulta.existe is True
    assert consulta.resultado == "A"
    assert consulta.cae and consulta.cae.isdigit() and len(consulta.cae) == 14
    assert consulta.vence and len(consulta.vence) == 8
    assert consulta.emision_tipo == "CAE"


def test_live_consultar_reports_absence_without_guessing(client):
    """A number without a visible record answers 602, not proof it is safe to reissue."""
    ultimo = client.ultimo_autorizado(1, 6)

    consulta = client.consultar_comprobante(1, 6, ultimo + 1000)

    assert consulta.existe is False
    assert consulta.cae is None


def test_live_solicitar_cae_authorizes_one_invoice(client):
    """Issue one Factura B for an unidentified final consumer.

    Irreversible: it mints a comprobante in homologación, which is why it needs
    ``ARCA_LIVE_EMITIR=1`` on top of ``ARCA_LIVE=1``. The comprobante has no
    fiscal validity, but the number is consumed and the CAE cannot be returned.

    The number is read fresh from ARCA, never hardcoded: a stale number is a
    rejection, and the point is to prove the engine's happy path end to end.
    """
    if not EMITIR:
        pytest.skip("set ARCA_LIVE_EMITIR=1 to issue a comprobante in homologación")

    hoy = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y%m%d")
    ultimo = client.ultimo_autorizado(1, 6)
    request = CaeRequest(
        pto_venta=1,
        cbte_tipo=6,
        cbte_desde=next_voucher_number(ultimo),
        cbte_fch=hoy,
        doc_tipo=99,
        doc_nro=0,
        imp_total=121.0,
        imp_neto=100.0,
        imp_iva=21.0,
        condicion_iva_receptor=CondicionIvaReceptor.CONSUMIDOR_FINAL,
        alicuotas=[AlicIva(5, 100.0, 21.0)],
    )

    # Tee the answer so the approval becomes a captured fixture instead of a
    # constructed one. Written outside the worktree on purpose.
    captured = {}
    original_post = client.http_post

    def http_post(url, payload, soap_action):
        answer = original_post(url, payload, soap_action)
        if soap_action.endswith("/FECAESolicitar"):
            captured["body"] = answer
        return answer

    client.http_post = http_post

    try:
        cae = client.solicitar_cae(request)
    except WsfeRejected as rejected:
        pytest.fail(
            f"ARCA rechazó la Factura B {request.cbte_desde} del PV 1: "
            f"{rejected.code} {rejected.message}"
        )
    except WsfeUncertain as uncertain:
        pytest.fail(
            "ARCA no confirmó el comprobante; reconciliar antes de reintentar: "
            f"{uncertain.message}"
        )

    assert cae.resultado == "A"
    assert cae.cae.isdigit() and len(cae.cae) == 14
    assert len(cae.vence) == 8

    destino = Path("/tmp/cae_aprobado.xml")
    destino.write_bytes(captured["body"])
    print(f"\nCAE {cae.cae} vence {cae.vence} — respuesta cruda en {destino}")
