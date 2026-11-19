"""Reading a comprobante back from ARCA.

This is the operation phase B needs before it can call an issuance "uncertain":
without it, an unknown outcome has no exit. Absence was measured live on
2026-11-12 and is ``Err 602`` with "No existen datos en nuestros registros para
los parametros ingresados", not an empty ``ResultGet``.
"""

from pathlib import Path

import pytest

from arca_integration.core.errors import WsfeError
from arca_integration.core.wsfe.consulta import (
    ConsultaAlicIva,
    build_consulta_request,
    parse_consulta_response,
)

WSFE_FIXTURES = Path(__file__).parent / "fixtures" / "wsfe"


def test_an_authorized_comprobante_is_read_back_with_its_cae():
    consulta = parse_consulta_response(
        (WSFE_FIXTURES / "comprobante_autorizado.xml").read_bytes()
    )

    assert consulta.existe is True
    assert consulta.resultado == "A"
    assert consulta.cae == "86461067947818"
    assert consulta.vence == "20261122"
    assert consulta.emision_tipo == "CAE"
    assert consulta.cbte_desde == 1
    assert consulta.cbte_hasta == 1
    assert consulta.imp_total == 121.0
    assert consulta.concepto == 1
    assert consulta.doc_tipo == 99
    assert consulta.doc_nro == 0
    assert consulta.cbte_fch == "20261112"
    assert consulta.imp_tot_conc == 0.0
    assert consulta.imp_neto == 100.0
    assert consulta.imp_op_ex == 0.0
    assert consulta.imp_trib == 0.0
    assert consulta.imp_iva == 21.0
    assert consulta.mon_id == "PES"
    assert consulta.mon_cotiz == 1.0
    assert consulta.condicion_iva_receptor == 5
    assert consulta.pto_venta == 1
    assert consulta.cbte_tipo == 6
    assert consulta.alicuotas == (ConsultaAlicIva(id=5, base_imp=100.0, importe=21.0),)


def test_missing_fiscal_fields_remain_missing_instead_of_becoming_defaults():
    body = (WSFE_FIXTURES / "comprobante_autorizado.xml").read_bytes()
    body = body.replace(b"<DocNro>0</DocNro>", b"")
    body = body.replace(
        b"<AlicIva><Id>5</Id><BaseImp>100</BaseImp><Importe>21</Importe></AlicIva>",
        b"",
    )

    consulta = parse_consulta_response(body)

    assert consulta.doc_nro is None
    assert consulta.alicuotas == ()


def test_602_reports_no_record_without_settling_the_issuance_attempt():
    """602 is an absent record at query time, not proof it cannot appear later."""
    consulta = parse_consulta_response(
        (WSFE_FIXTURES / "comprobante_inexistente.xml").read_bytes()
    )

    assert consulta.existe is False
    assert consulta.cae is None
    assert consulta.resultado is None


def test_an_error_other_than_602_is_not_read_as_absence():
    """Only 602 means "no records". Anything else leaves the outcome unknown."""
    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        b"<soap:Body><FECompConsultarResponse>"
        b"<FECompConsultarResult><Errors><Err><Code>10015</Code>"
        b"<Msg>Error interno.</Msg></Err></Errors></FECompConsultarResult>"
        b"</FECompConsultarResponse></soap:Body></soap:Envelope>"
    )

    with pytest.raises(WsfeError) as excinfo:
        parse_consulta_response(body)

    assert excinfo.value.code == 10015


def test_an_answer_that_is_neither_a_record_nor_an_error_is_refused():
    """Never guess: an unclassifiable answer is an error, not an absence."""
    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        b"<soap:Body><FECompConsultarResponse>"
        b"<FECompConsultarResult/></FECompConsultarResponse>"
        b"</soap:Body></soap:Envelope>"
    )

    with pytest.raises(WsfeError):
        parse_consulta_response(body)


def test_the_request_carries_the_field_order_the_manual_requires():
    """``CbteTipo, CbteNro, PtoVta``: ARCA's order, not ours."""
    from datetime import datetime, timezone

    from arca_integration.core.wsaa import Ticket
    from arca_integration.core.wsfe.xml import Auth

    auth = Auth(
        Ticket(token="t", sign="s", expires_at=datetime.now(timezone.utc)),
        "20000000001",
    )
    body = build_consulta_request(auth, pto_venta=1, cbte_tipo=6, cbte_nro=999).decode()

    assert (
        "<ar:FeCompConsReq><ar:CbteTipo>6</ar:CbteTipo><ar:CbteNro>999</ar:CbteNro>"
        "<ar:PtoVta>1</ar:PtoVta></ar:FeCompConsReq>"
    ) in body
    assert "FECompConsultar" in body
