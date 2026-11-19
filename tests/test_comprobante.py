"""The fiscal logic of one emission, with no Frappe, no site and no network.

The tests are ordered by what a mistake would cost. The cheapest thing to get
wrong is a message; the most expensive is the number, because ARCA's numbering
is consumed and ``10016`` cannot be undone by retrying.
"""

from datetime import date
from decimal import Decimal

import pytest

from arca_integration.core.comprobante import (
    ESTADOS,
    TRANSICIONES,
    AlicuotaImporte,
    ComprobanteError,

    Estado,
    Importes,
    clase_de_comprobante,
    condicion_iva_permitida,
    construir_solicitud,
    emitir,
    exigir_transicion,
    formatear_fecha,
    reconciliar,
    transicion_permitida,
    validate_amounts,
)
from arca_integration.core.errors import AuthError, TransportError, WsfeRejected, WsfeUncertain
from arca_integration.core.wsfe.cae import CaeRequest
from arca_integration.core.wsfe.consulta import ConsultaAlicIva, ConsultaComprobante
from arca_integration.core.wsfe.types import Cae

AUTORIZADO = Cae(cae="86461067947818", vence="20261122", resultado="A")


def importes(valor: str = "121.00") -> Importes:
    """A 100,00 neto + 21 % IVA request, or one with the given total."""
    return Importes(
        neto=Decimal("100.00"),
        iva=Decimal("21.00"),
        trib=Decimal("0.00"),
        total=Decimal(valor),
        alicuotas=(AlicuotaImporte(5, Decimal("100.00"), Decimal("21.00")),),
    )


def solicitud(numero: int, *, doc_tipo: int = 99, doc_nro: int = 0):
    return construir_solicitud(
        pto_venta=1,
        cbte_tipo=6,
        cbte_nro=numero,
        cbte_fch=date(2026, 11, 12),
        condicion_iva_receptor=5,
        importes=importes(),
        doc_tipo=doc_tipo,
        doc_nro=doc_nro,
    )


def consulta_autorizada(solicitud_esperada: CaeRequest, **cambios):
    alicuotas = tuple(
        ConsultaAlicIva(
            id=row.id,
            base_imp=row.base_imp,
            importe=row.importe,
        )
        for row in solicitud_esperada.alicuotas
    )
    valores = dict(
        existe=True,
        resultado="A",
        cae="86461067947818",
        vence="20261122",
        emision_tipo="CAE",
        cbte_desde=solicitud_esperada.cbte_desde,
        cbte_hasta=solicitud_esperada.cbte_desde,
        imp_total=solicitud_esperada.imp_total,
        concepto=1,
        doc_tipo=solicitud_esperada.doc_tipo,
        doc_nro=solicitud_esperada.doc_nro,
        cbte_fch=solicitud_esperada.cbte_fch,
        imp_tot_conc=solicitud_esperada.imp_tot_conc,
        imp_neto=solicitud_esperada.imp_neto,
        imp_op_ex=solicitud_esperada.imp_op_ex,
        imp_trib=solicitud_esperada.imp_trib,
        imp_iva=solicitud_esperada.imp_iva,
        mon_id=solicitud_esperada.mon_id,
        mon_cotiz=solicitud_esperada.mon_cotiz,
        condicion_iva_receptor=int(solicitud_esperada.condicion_iva_receptor),
        pto_venta=solicitud_esperada.pto_venta,
        cbte_tipo=solicitud_esperada.cbte_tipo,
        alicuotas=alicuotas,
        raw=b"",
    )
    valores.update(cambios)
    return ConsultaComprobante(**valores)


class ClienteFalso:
    """A ``WsfeClient`` stand-in that remembers everything it was asked."""

    def __init__(self, ultimo: int, respuestas: list):
        self.ultimo = ultimo
        self.respuestas = list(respuestas)
        self.solicitudes = []
        self.consultas = []

    def ultimo_autorizado(self, pto_venta, cbte_tipo):
        self.numeracion_consultada = (pto_venta, cbte_tipo)
        return self.ultimo

    def solicitar_cae(self, request):
        self.solicitudes.append(request)
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, BaseException):
            raise respuesta
        # ARCA advances its counter only when it actually authorized something.
        self.ultimo = request.cbte_desde
        return respuesta

    def consultar_comprobante(self, pto_venta, cbte_tipo, numero):
        self.consultas.append((pto_venta, cbte_tipo, numero))
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, BaseException):
            raise respuesta
        return respuesta


def test_the_states_are_the_ones_the_doctype_will_offer():
    assert ESTADOS == ("Borrador", "Solicitando", "Autorizado", "Rechazado", "Incierto")


# --- numbering: the expensive mistake -------------------------------------


def test_the_first_attempt_asks_for_one_after_the_last_authorized():
    cliente = ClienteFalso(ultimo=0, respuestas=[AUTORIZADO])

    resultado = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert resultado.numero == 1
    assert cliente.numeracion_consultada == (1, 6)


def test_a_second_attempt_reads_the_number_again_instead_of_reusing_it():
    """A cached number drifts the moment anyone else issues from that point of sale."""
    cliente = ClienteFalso(ultimo=4, respuestas=[AUTORIZADO])

    primero = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    # Something else issued in between: ARCA now reports 6 as the last authorized.
    cliente.ultimo = 6
    cliente.respuestas.append(AUTORIZADO)
    segundo = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert (primero.numero, segundo.numero) == (5, 7)
    assert [enviada.cbte_desde for enviada in cliente.solicitudes] == [5, 7]


# --- the number has to be durable before the request leaves -----------------
#
# ``FECAESolicitar`` can be received and answered while the caller dies holding
# the answer. The only way to reconcile that is to know which number was sent,
# and it is only knowable if it was written before the request left.


def test_the_assigned_number_is_reported_before_the_request_is_sent():
    visto = []

    def al_asignar_numero(numero):
        visto.append(("asignado", numero, len(cliente.solicitudes)))

    cliente = ClienteFalso(ultimo=8, respuestas=[AUTORIZADO])

    resultado = emitir(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        solicitud_para=solicitud,
        al_asignar_numero=al_asignar_numero,
    )

    assert visto == [("asignado", 9, 0)]
    assert resultado.numero == 9


def test_a_request_that_cannot_be_built_never_reports_a_number():
    """Validation happens before the number is announced, so a refusal costs nothing."""
    visto = []
    cliente = ClienteFalso(ultimo=0, respuestas=[])

    with pytest.raises(ComprobanteError):
        emitir(
            cliente,
            pto_venta=1,
            cbte_tipo=3,
            solicitud_para=lambda numero: construir_solicitud(
                pto_venta=1,
                cbte_tipo=3,
                cbte_nro=numero,
                cbte_fch=date(2026, 11, 12),
                condicion_iva_receptor=5,
                importes=importes(),
            ),
            al_asignar_numero=visto.append,
        )

    assert visto == []
    assert cliente.solicitudes == []


def test_a_number_that_cannot_be_persisted_stops_the_request():
    """If the caller cannot store it, sending would create an untraceable number."""

    def falla(numero):
        raise RuntimeError(f"no se pudo guardar el número {numero}")

    cliente = ClienteFalso(ultimo=4, respuestas=[AUTORIZADO])

    with pytest.raises(RuntimeError):
        emitir(
            cliente,
            pto_venta=1,
            cbte_tipo=6,
            solicitud_para=solicitud,
            al_asignar_numero=falla,
        )

    assert cliente.solicitudes == []



def test_a_rejection_does_not_consume_the_number():
    """ARCA did not authorize anything, so the number is still free."""
    cliente = ClienteFalso(ultimo=0, respuestas=[WsfeRejected("Importes mal.", code=10048)])

    primero = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)
    cliente.respuestas.append(AUTORIZADO)
    segundo = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert (primero.estado, primero.numero) == (Estado.RECHAZADO, 1)
    assert (segundo.estado, segundo.numero) == (Estado.AUTORIZADO, 1)


def test_the_request_that_was_sent_comes_back_for_the_audit_trail():
    cliente = ClienteFalso(ultimo=0, respuestas=[AUTORIZADO])

    resultado = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert resultado.solicitud is cliente.solicitudes[0]
    assert resultado.solicitud.cbte_desde == resultado.numero
    assert resultado.solicitud.cbte_fch == "20261112"


# --- outcomes: never claim more than ARCA said -----------------------------


def test_an_authorized_answer_carries_the_cae():
    cliente = ClienteFalso(ultimo=0, respuestas=[AUTORIZADO])

    resultado = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert resultado.estado is Estado.AUTORIZADO
    assert resultado.cae.cae == "86461067947818"
    assert resultado.error is None


def test_a_rejection_is_terminal_for_that_attempt():
    cliente = ClienteFalso(ultimo=0, respuestas=[WsfeRejected("Importes mal.", code=10048)])

    resultado = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert resultado.estado is Estado.RECHAZADO
    assert resultado.cae is None
    assert resultado.error.code == 10048


def test_a_partial_answer_is_uncertain_and_never_authorized():
    cliente = ClienteFalso(ultimo=0, respuestas=[WsfeUncertain("No se pudo determinar.")])

    resultado = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert resultado.estado is Estado.INCIERTO
    assert resultado.cae is None


def test_a_transport_error_during_issuance_is_uncertain():
    """Not rejection: we cannot tell "never sent" from "sent, no answer"."""
    cliente = ClienteFalso(ultimo=0, respuestas=[TransportError("timeout")])

    resultado = emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert resultado.estado is Estado.INCIERTO
    assert resultado.cae is None


def test_a_failure_before_the_request_is_sent_changes_nothing():
    """Failing to read the numbering means no comprobante was ever offered."""

    class SinRed(ClienteFalso):
        def ultimo_autorizado(self, pto_venta, cbte_tipo):
            raise TransportError("sin red")

    cliente = SinRed(ultimo=0, respuestas=[])

    with pytest.raises(TransportError):
        emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert cliente.solicitudes == []


def test_an_authentication_failure_does_not_consume_a_number():
    """The ticket is acquired before the comprobante is offered: nothing was sent."""

    class SinTicket(ClienteFalso):
        def solicitar_cae(self, request):
            raise AuthError("certificado vencido")

    cliente = SinTicket(ultimo=0, respuestas=[])

    with pytest.raises(AuthError):
        emitir(cliente, pto_venta=1, cbte_tipo=6, solicitud_para=solicitud)

    assert cliente.solicitudes == []


# --- validation: refuse locally, before the network ------------------------


def test_amounts_that_do_not_recompose_are_refused_before_any_request():
    cliente = ClienteFalso(ultimo=0, respuestas=[])
    malo = importes("130.00")

    with pytest.raises(ComprobanteError):
        emitir(
            cliente,
            pto_venta=1,
            cbte_tipo=6,
            solicitud_para=lambda n: construir_solicitud(
                pto_venta=1,
                cbte_tipo=6,
                cbte_nro=n,
                cbte_fch=date(2026, 11, 12),
                condicion_iva_receptor=5,
                importes=malo,
            ),
        )

    assert cliente.solicitudes == []


def test_the_alicuota_bases_must_add_up_to_the_net_amount():
    with pytest.raises(ComprobanteError):
        validate_amounts(
            Importes(
                neto=Decimal("100.00"),
                iva=Decimal("21.00"),
                trib=Decimal("0.00"),
                total=Decimal("121.00"),
                alicuotas=(AlicuotaImporte(5, Decimal("90.00"), Decimal("21.00")),),
            )
        )


def test_the_alicuota_amounts_must_add_up_to_the_iva():
    with pytest.raises(ComprobanteError):
        validate_amounts(
            Importes(
                neto=Decimal("100.00"),
                iva=Decimal("21.00"),
                trib=Decimal("0.00"),
                total=Decimal("121.00"),
                alicuotas=(AlicuotaImporte(5, Decimal("100.00"), Decimal("10.50")),),
            )
        )


def test_net_income_without_alicuotas_is_refused():
    """A tax base with no detail is an amount ARCA cannot validate."""
    with pytest.raises(ComprobanteError):
        validate_amounts(
            Importes(
                neto=Decimal("100.00"),
                iva=Decimal("21.00"),
                trib=Decimal("0.00"),
                total=Decimal("121.00"),
                alicuotas=(),
            )
        )


def test_a_zero_rate_is_still_declared_as_an_alicuota():
    """0 % is an alícuota with importe 0, not the absence of a detail."""
    validate_amounts(
        Importes(
            neto=Decimal("100.00"),
            iva=Decimal("0.00"),
            trib=Decimal("0.00"),
            total=Decimal("100.00"),
            alicuotas=(AlicuotaImporte(3, Decimal("100.00"), Decimal("0.00")),),
        )
    )


def test_a_cent_of_rounding_is_accepted():
    """ARCA tolerates 0.01 % or 0.01 absolute (``10048``)."""
    validate_amounts(importes("121.01"))


def test_two_cents_are_not_rounding():
    with pytest.raises(ComprobanteError):
        validate_amounts(importes("121.02"))


def test_a_total_of_zero_is_refused():
    with pytest.raises(ComprobanteError):
        validate_amounts(
            Importes(
                neto=Decimal("0.00"),
                iva=Decimal("0.00"),
                trib=Decimal("0.00"),
                total=Decimal("0.00"),
                alicuotas=(),
            )
        )


def test_a_negative_amount_is_refused():
    with pytest.raises(ComprobanteError):
        validate_amounts(
            Importes(
                neto=Decimal("-100.00"),
                iva=Decimal("0.00"),
                trib=Decimal("0.00"),
                total=Decimal("100.00"),
                alicuotas=(),
            )
        )


# --- comprobante class and receiver condition -----------------------------


def test_the_receiver_condition_is_checked_against_the_class():
    assert condicion_iva_permitida(1, 1) is True
    assert condicion_iva_permitida(1, 5) is False
    assert condicion_iva_permitida(6, 5) is True
    assert condicion_iva_permitida(6, 6) is False


def test_a_class_without_a_pinned_pairing_is_not_refused_locally():
    """For class C ARCA decides: the table here is documentation, not a contract."""
    assert condicion_iva_permitida(11, 5) is None
    assert condicion_iva_permitida(99, 5) is None


def test_an_impossible_condition_is_refused_before_the_network():
    with pytest.raises(ComprobanteError):
        construir_solicitud(
            pto_venta=1,
            cbte_tipo=6,
            cbte_nro=1,
            cbte_fch=date(2026, 11, 12),
            condicion_iva_receptor=6,
            importes=importes(),
        )


def test_a_condition_outside_arca_s_catalog_is_refused_locally():
    """99 would come back as 10242; refuse it before it leaves the process."""
    with pytest.raises(ComprobanteError):
        construir_solicitud(
            pto_venta=1,
            cbte_tipo=6,
            cbte_nro=1,
            cbte_fch=date(2026, 11, 12),
            condicion_iva_receptor=99,
            importes=importes(),
        )


def test_a_condition_outside_the_original_four_goes_through():
    """The enum used to stop at four values, so class B was unreachable."""
    request = construir_solicitud(
        pto_venta=1,
        cbte_tipo=6,
        cbte_nro=1,
        cbte_fch=date(2026, 11, 12),
        condicion_iva_receptor=15,
        importes=importes(),
    )

    assert int(request.condicion_iva_receptor) == 15


# --- the request itself ---------------------------------------------------


def test_the_request_carries_the_dates_and_the_consumer_defaults():
    request = construir_solicitud(
        pto_venta=1,
        cbte_tipo=6,
        cbte_nro=7,
        cbte_fch=date(2026, 11, 12),
        condicion_iva_receptor=5,
        importes=importes(),
    )

    assert request.cbte_fch == "20261112"
    assert request.doc_tipo == 99
    assert request.doc_nro == 0
    assert request.mon_id == "PES"
    assert request.mon_cotiz == 1.0
    assert request.alicuotas[0].id == 5


def test_an_unidentified_consumer_always_sends_document_number_zero():
    assert solicitud(1, doc_nro=20000000001).doc_nro == 0


def test_the_date_accepts_a_date_an_iso_string_and_compact_form():
    assert formatear_fecha(date(2026, 11, 12)) == "20261112"
    assert formatear_fecha("2026-11-12") == "20261112"
    assert formatear_fecha("20261112") == "20261112"


def test_an_ambiguous_date_is_refused():
    with pytest.raises(ComprobanteError):
        formatear_fecha("12/11/2026")


# --- reconciliation: the only exit from Incierto --------------------------


def test_reconciliation_of_a_found_authorized_comprobante_authorizes_it():
    request = solicitud(1)
    cliente = ClienteFalso(
        ultimo=1,
        respuestas=[consulta_autorizada(request)],
    )

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=1,
        solicitud_esperada=request,
    )

    assert resultado.estado is Estado.AUTORIZADO
    assert resultado.cae == "86461067947818"
    assert cliente.consultas == [(1, 6, 1)]


def test_reconciliation_with_a_different_amount_stays_uncertain():
    request = solicitud(1)
    cliente = ClienteFalso(
        ultimo=1,
        respuestas=[consulta_autorizada(request, imp_total=122.0)],
    )

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=1,
        solicitud_esperada=request,
    )

    assert resultado.estado is Estado.INCIERTO
    assert resultado.cae is None
    assert "total" in resultado.error.detail


def test_reconciliation_without_complete_fiscal_data_stays_uncertain():
    request = solicitud(1)
    cliente = ClienteFalso(
        ultimo=1,
        respuestas=[consulta_autorizada(request, doc_nro=None)],
    )

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=1,
        solicitud_esperada=request,
    )

    assert resultado.estado is Estado.INCIERTO
    assert resultado.cae is None
    assert "número de documento" in resultado.error.detail


def test_reconciliation_of_an_absent_comprobante_stays_uncertain():
    cliente = ClienteFalso(
        ultimo=0, respuestas=[ConsultaComprobante(existe=False, raw=b"")]
    )

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=1,
        solicitud_esperada=solicitud(1),
    )

    assert resultado.estado is Estado.INCIERTO
    assert resultado.cae is None
    assert resultado.consulta.existe is False
    assert isinstance(resultado.error, WsfeUncertain)
    assert "602" in resultado.error.message


def test_reconciliation_that_finds_something_else_stays_uncertain():
    """A record without an authorization is not an authorization."""
    cliente = ClienteFalso(
        ultimo=1,
        respuestas=[
            ConsultaComprobante(
                existe=True, resultado="R", cae=None, vence=None, raw=b""
            )
        ],
    )

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=1,
        solicitud_esperada=solicitud(1),
    )

    assert resultado.estado is Estado.INCIERTO
    assert resultado.cae is None


def test_reconciliation_survives_a_failure_and_stays_uncertain():
    cliente = ClienteFalso(ultimo=1, respuestas=[TransportError("timeout")])

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=1,
        solicitud_esperada=solicitud(1),
    )

    assert resultado.estado is Estado.INCIERTO
    assert isinstance(resultado.error, TransportError)


def test_reconciliation_never_renumbers():
    cliente = ClienteFalso(ultimo=9, respuestas=[ConsultaComprobante(existe=False, raw=b"")])

    resultado = reconciliar(
        cliente,
        pto_venta=1,
        cbte_tipo=6,
        numero=3,
        solicitud_esperada=solicitud(3),
    )

    assert resultado.numero == 3
    assert cliente.consultas == [(1, 6, 3)]


# --- the state machine ------------------------------------------------------
#
# One module writes ``estado``. Every move is listed here so a new code path
# cannot invent one, and so "authorized" is provably terminal.


def test_every_transition_is_listed_explicitly():
    assert TRANSICIONES[Estado.BORRADOR] == frozenset({Estado.SOLICITANDO})
    assert TRANSICIONES[Estado.SOLICITANDO] == frozenset(
        {Estado.AUTORIZADO, Estado.RECHAZADO, Estado.INCIERTO, Estado.BORRADOR}
    )
    assert TRANSICIONES[Estado.RECHAZADO] == frozenset({Estado.SOLICITANDO})
    assert TRANSICIONES[Estado.INCIERTO] == frozenset(
        {Estado.AUTORIZADO, Estado.INCIERTO}
    )
    assert TRANSICIONES[Estado.AUTORIZADO] == frozenset()


def test_an_authorized_comprobante_never_moves_again():
    for destino in Estado:
        assert not transicion_permitida(Estado.AUTORIZADO, destino)

    with pytest.raises(ComprobanteError):
        exigir_transicion(Estado.AUTORIZADO, Estado.SOLICITANDO)


def test_an_uncertain_comprobante_is_not_emitted_again_directly():
    assert not transicion_permitida(Estado.INCIERTO, Estado.SOLICITANDO)


def test_a_transition_outside_the_table_names_both_states():
    with pytest.raises(ComprobanteError) as excinfo:
        exigir_transicion(Estado.BORRADOR, Estado.AUTORIZADO)

    assert "Borrador" in str(excinfo.value)
    assert "Autorizado" in str(excinfo.value)


def test_a_state_stored_as_text_is_accepted():
    """The DocType stores the values, not the enum members."""
    assert transicion_permitida("Borrador", "Solicitando")
    assert not transicion_permitida("Autorizado", "Solicitando")


# --- the class of a comprobante ---------------------------------------------


def test_only_the_two_measured_classes_have_a_letter():
    assert clase_de_comprobante(1) == "A"
    assert clase_de_comprobante(6) == "B"


def test_an_unmeasured_class_is_refused_naming_what_is_available():
    for otro in (2, 3, 11, 51, 0, None):
        with pytest.raises(ComprobanteError) as excinfo:
            clase_de_comprobante(otro)

        assert "Factura A" in str(excinfo.value)
        assert "Factura B" in str(excinfo.value)


def test_an_unmeasured_class_never_reaches_the_network():
    cliente = ClienteFalso(ultimo=0, respuestas=[])

    with pytest.raises(ComprobanteError):
        emitir(
            cliente,
            pto_venta=1,
            cbte_tipo=3,
            solicitud_para=lambda n: construir_solicitud(
                pto_venta=1,
                cbte_tipo=3,
                cbte_nro=n,
                cbte_fch=date(2026, 11, 12),
                condicion_iva_receptor=5,
                importes=importes(),
            ),
        )

    assert cliente.solicitudes == []


def test_a_point_of_sale_below_one_is_refused_before_the_network():
    with pytest.raises(ComprobanteError) as excinfo:
        construir_solicitud(
            pto_venta=0,
            cbte_tipo=6,
            cbte_nro=1,
            cbte_fch=date(2026, 11, 12),
            condicion_iva_receptor=5,
            importes=importes(),
        )

    assert "punto de venta" in str(excinfo.value).lower()
