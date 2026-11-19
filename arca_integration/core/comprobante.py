"""The fiscal logic of one comprobante emission, independent of Frappe.

Nothing here imports ``frappe``: the DocType layer is expected to be glue that
resolves settings, calls these functions, and stores the result. That keeps the
expensive mistakes testable without a site, a database or a network.

Two rules hold the design together.

The number is read from ARCA on every attempt and never cached. ARCA's numbering
is consumed by whoever issues, so a locally remembered number drifts the moment
someone else uses the same point of sale.

An outcome is only ``Autorizado`` when ARCA returned a CAE. A transport failure
is not a rejection — we cannot tell "never sent" from "sent, no answer" — and an
answer we cannot classify leaves the comprobante ``Incierto``, which is the only
state that later reconciliation can resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable

from .errors import ArcaError, AuthError, TransportError, WsfeError, WsfeRejected, WsfeUncertain
from .wsfe.cae import AlicIva, CaeRequest, CONCEPTO_PRODUCTOS
from .wsfe.consulta import ConsultaComprobante
from .wsfe.numbering import next_voucher_number
from .wsfe.types import Cae, CondicionIvaReceptor

TOLERANCIA_RELATIVA = Decimal("0.0001")
TOLERANCIA_ABSOLUTA = Decimal("0.01")

CENTAVO = Decimal("0.01")

#: Receiver VAT conditions ARCA accepts for each comprobante class, observed in
#: homologación. A class missing here is left to ARCA: the table is
#: documentation, not a contract worth blocking a valid emission with.
#:
#: Deliberately not built from ``FEParamGetCondicionIvaReceptor.Cmp_Clase``,
#: which places ``Consumidor Final`` (5) in class ``C/49`` even though a Factura B
#: with condition 5 was authorized. The measured authorization wins.
CONDICIONES_VERIFICADAS: dict[int, frozenset[int]] = {
    1: frozenset({1, 6, 13, 16}),  # Factura A
    6: frozenset({4, 5, 7, 8, 9, 10, 15}),  # Factura B
}

#: The comprobante classes that were measured, and the letter each one prints.
#: A type outside this table is refused rather than guessed: the class decides
#: which document the receiver gets, and a valid CAE for the wrong one is worse
#: than a rejection.
TIPOS_DE_COMPROBANTE: dict[int, str] = {1: "A", 6: "B"}


class Estado(str, Enum):
    """The lifecycle of one emission attempt.

    The values are the DocType's ``Select`` options, so the database, the form
    and this module cannot drift apart.
    """

    BORRADOR = "Borrador"
    SOLICITANDO = "Solicitando"
    AUTORIZADO = "Autorizado"
    RECHAZADO = "Rechazado"
    INCIERTO = "Incierto"


ESTADOS = tuple(estado.value for estado in Estado)

#: Every allowed move between states, written out on purpose. One module writes
#: ``estado``; a path that is not listed here does not exist.
#:
#: ``Solicitando → Borrador`` is only for an attempt proven not to have been
#: sent: no number was assigned, or a failure happened before the WSFE request.
#: Neither case consumes a number, so neither freezes the document for good.
#:
#: ``Incierto → Incierto`` is a reconciliation that settled nothing: the operator
#: asked, ARCA still could not say, and the attempt stays where it was.
TRANSICIONES: dict[Estado, frozenset[Estado]] = {
    Estado.BORRADOR: frozenset({Estado.SOLICITANDO}),
    Estado.SOLICITANDO: frozenset(
        {Estado.AUTORIZADO, Estado.RECHAZADO, Estado.INCIERTO, Estado.BORRADOR}
    ),
    Estado.RECHAZADO: frozenset({Estado.SOLICITANDO}),
    Estado.INCIERTO: frozenset({Estado.AUTORIZADO, Estado.INCIERTO}),
    Estado.AUTORIZADO: frozenset(),
}


class ComprobanteError(ArcaError):
    """A request that must not be sent: ARCA would only reject it.

    Raised before the network call, so nothing is consumed and the number stays
    free. Distinct from ``WsfeRejected``, which is ARCA's answer to something
    that was actually sent.
    """


@dataclass(frozen=True)
class AlicuotaImporte:
    """One VAT line. ``id`` is ARCA's alícuota id, not a percentage."""

    id: int
    base: Decimal
    importe: Decimal


@dataclass(frozen=True)
class Importes:
    """The money of one comprobante, before it becomes an ARCA request."""

    neto: Decimal
    iva: Decimal
    trib: Decimal
    total: Decimal
    alicuotas: tuple[AlicuotaImporte, ...] = ()


@dataclass(frozen=True)
class ResultadoEmision:
    """What came out of one attempt, ready to be stored on the document."""

    estado: Estado
    numero: int
    solicitud: CaeRequest
    cae: Cae | None = None
    error: ArcaError | None = None


@dataclass(frozen=True)
class Conciliacion:
    """What reconciliation found, ready to be stored on the document."""

    estado: Estado
    numero: int
    consulta: ConsultaComprobante | None = None
    cae: str | None = None
    error: ArcaError | None = None


SolicitudPara = Callable[[int], CaeRequest]


def validate_amounts(importes: Importes) -> None:
    """Refuse locally what ARCA would reject with ``10048``.

    The tolerance is ARCA's: relative error up to 0.01 % or absolute up to 0.01.
    Validating with the same rule means the operator sees which amount is wrong
    instead of a rejection code.
    """
    if importes.total <= 0:
        raise ComprobanteError("El importe total del comprobante debe ser mayor que cero.")

    for nombre, valor in (
        ("neto", importes.neto),
        ("IVA", importes.iva),
        ("tributos", importes.trib),
    ):
        if valor < 0:
            raise ComprobanteError(f"El importe de {nombre} no puede ser negativo.")

    suma = importes.neto + importes.iva + importes.trib
    if not _coincide(suma, importes.total):
        raise ComprobanteError(
            "Los importes del comprobante no cierran: "
            f"neto {importes.neto} + IVA {importes.iva} + tributos {importes.trib} "
            f"= {suma}, y el total informado es {importes.total}. "
            "ARCA lo rechazaría con 10048."
        )

    if importes.neto != 0 and not importes.alicuotas:
        raise ComprobanteError(
            "El comprobante tiene importe neto gravado pero no detalla alícuotas. "
            "ARCA no puede validar el IVA sin el detalle, y una alícuota al 0 % "
            "también se informa."
        )

    bases = sum((alicuota.base for alicuota in importes.alicuotas), Decimal("0.00"))
    if not _coincide(bases, importes.neto):
        raise ComprobanteError(
            f"Las bases de las alícuotas suman {bases} y el neto gravado es "
            f"{importes.neto}."
        )

    detalle = sum((alicuota.importe for alicuota in importes.alicuotas), Decimal("0.00"))
    if not _coincide(detalle, importes.iva):
        raise ComprobanteError(
            f"Las alícuotas suman {detalle} de IVA y el comprobante informa "
            f"{importes.iva}."
        )

    for alicuota in importes.alicuotas:
        if alicuota.base < 0 or alicuota.importe < 0:
            raise ComprobanteError("Una alícuota no puede tener base o importe negativo.")


def condicion_iva_permitida(cbte_tipo: int, condicion: int) -> bool | None:
    """Whether ARCA accepts that receiver condition for that class.

    ``None`` means this module has no pinned table for the class and ARCA is the
    only authority; callers must not read ``None`` as a refusal.
    """
    permitidas = CONDICIONES_VERIFICADAS.get(int(cbte_tipo))
    if permitidas is None:
        return None
    return int(condicion) in permitidas


def _estado(valor: Estado | str) -> Estado:
    try:
        return valor if isinstance(valor, Estado) else Estado(str(valor))
    except ValueError:
        raise ComprobanteError(f"El estado {valor!r} no existe en el comprobante.") from None


def transicion_permitida(desde: Estado | str, hasta: Estado | str) -> bool:
    """Whether the engine may move the comprobante from one state to another."""
    return _estado(hasta) in TRANSICIONES[_estado(desde)]


def exigir_transicion(desde: Estado | str, hasta: Estado | str) -> None:
    """Refuse a move the state machine does not list, naming both states."""
    if transicion_permitida(desde, hasta):
        return

    raise ComprobanteError(
        f"El comprobante no puede pasar de {_estado(desde).value} a "
        f"{_estado(hasta).value}. Los estados posibles desde "
        f"{_estado(desde).value} son: "
        f"{', '.join(sorted(estado.value for estado in TRANSICIONES[_estado(desde)])) or 'ninguno'}."
    )


def clase_de_comprobante(cbte_tipo: int) -> str:
    """The letter of a measured comprobante class, or a refusal naming them."""
    try:
        return TIPOS_DE_COMPROBANTE[int(cbte_tipo)]
    except (KeyError, TypeError, ValueError):
        disponibles = ", ".join(
            f"{tipo} (Factura {letra})" for tipo, letra in TIPOS_DE_COMPROBANTE.items()
        )
        raise ComprobanteError(
            f"El tipo de comprobante {cbte_tipo!r} no fue medido contra ARCA. "
            f"Los tipos disponibles son: {disponibles}."
        ) from None


def formatear_fecha(value: str | date | datetime) -> str:
    """ARCA's ``YYYYMMDD``, refusing anything ambiguous like ``12/11/2026``."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")

    texto = str(value or "").strip()
    if len(texto) == 8 and texto.isdigit():
        return texto
    try:
        return date.fromisoformat(texto).strftime("%Y%m%d")
    except ValueError:
        raise ComprobanteError(
            f"Fecha de comprobante inválida: {texto!r}. Se espera AAAA-MM-DD."
        ) from None


def construir_solicitud(
    *,
    pto_venta: int,
    cbte_tipo: int,
    cbte_nro: int,
    cbte_fch: str | date | datetime,
    condicion_iva_receptor: int,
    importes: Importes,
    doc_tipo: int = 99,
    doc_nro: int = 0,
    mon_id: str = "PES",
    mon_cotiz: Decimal = Decimal("1.00"),
) -> CaeRequest:
    """Build the request for one comprobante, refusing what ARCA would refuse.

    ``doc_tipo=99`` with ``doc_nro=0`` is ARCA's "unidentified final consumer".
    """
    if cbte_nro < 1:
        raise ComprobanteError(
            f"El número de comprobante debe ser mayor que cero, no {cbte_nro}."
        )

    try:
        punto = int(pto_venta)
        tipo = int(cbte_tipo)
    except (TypeError, ValueError):
        raise ComprobanteError(
            "El punto de venta y el tipo de comprobante deben ser números enteros."
        ) from None

    if punto < 1:
        raise ComprobanteError(
            f"El punto de venta debe ser mayor que cero, no {punto}."
        )

    clase_de_comprobante(tipo)
    validate_amounts(importes)

    try:
        condicion = CondicionIvaReceptor(int(condicion_iva_receptor))
    except ValueError:
        raise ComprobanteError(
            f"La condición frente al IVA {condicion_iva_receptor} del receptor no "
            "existe en el catálogo de ARCA."
        ) from None

    if condicion_iva_permitida(tipo, condicion) is False:
        raise ComprobanteError(
            f"La condición frente al IVA {condicion_iva_receptor} del receptor no es "
            f"válida para el comprobante tipo {tipo}. ARCA la rechazaría con 10243."
        )

    tipo_documento = int(doc_tipo)
    numero_documento = 0 if tipo_documento == 99 else int(doc_nro)

    return CaeRequest(
        pto_venta=punto,
        cbte_tipo=tipo,
        cbte_desde=int(cbte_nro),
        cbte_fch=formatear_fecha(cbte_fch),
        doc_tipo=tipo_documento,
        doc_nro=numero_documento,
        imp_total=_centavos(importes.total),
        imp_neto=_centavos(importes.neto),
        imp_iva=_centavos(importes.iva),
        imp_trib=_centavos(importes.trib),
        condicion_iva_receptor=condicion,
        alicuotas=[
            AlicIva(
                id=int(alicuota.id),
                base_imp=_centavos(alicuota.base),
                importe=_centavos(alicuota.importe),
            )
            for alicuota in importes.alicuotas
        ],
        mon_id=mon_id,
        mon_cotiz=float(mon_cotiz),
    )


def emitir(
    client,
    *,
    pto_venta: int,
    cbte_tipo: int,
    solicitud_para: SolicitudPara,
    al_asignar_numero: Callable[[int], None] | None = None,
) -> ResultadoEmision:
    """Read the number from ARCA, build the request and send it once.

    ``solicitud_para`` receives the number ARCA reports as next, so validation
    runs after the number is known and before anything leaves the process.

    ``al_asignar_numero`` runs once the number is known *and* the request is
    valid, and **before** the request is sent. It exists so the caller can
    persist the number: if the process dies holding ARCA's answer, that stored
    number is the only way to reconcile the attempt. A callback that raises
    stops the emission — a number nobody recorded must not be sent.

    Never raises for an issuance outcome: a rejection, an uncertain answer and a
    transport failure all come back as a state to store. Only failures that
    happened before the comprobante was offered — reading the numbering, the
    WSAA ticket, storing the number — propagate, because those consumed nothing.
    """
    ultimo = client.ultimo_autorizado(pto_venta, cbte_tipo)
    numero = next_voucher_number(ultimo)
    solicitud = solicitud_para(numero)

    if al_asignar_numero is not None:
        al_asignar_numero(numero)

    try:
        cae = client.solicitar_cae(solicitud)
    except WsfeRejected as exc:
        return ResultadoEmision(Estado.RECHAZADO, numero, solicitud, error=exc)
    except (WsfeUncertain, TransportError) as exc:
        return ResultadoEmision(Estado.INCIERTO, numero, solicitud, error=exc)

    return ResultadoEmision(Estado.AUTORIZADO, numero, solicitud, cae=cae)


def reconciliar(
    client,
    *,
    pto_venta: int,
    cbte_tipo: int,
    numero: int,
    solicitud_esperada: CaeRequest,
) -> Conciliacion:
    """Ask ARCA whether that number exists, and settle the state from the answer.

    The number is never recomputed: reconciliation is about the attempt that
    already happened, not about the next one. A failure to read the record or a
    602 answer also stays ``Incierto`` — neither proves it is safe to reissue.
    """
    try:
        consulta = client.consultar_comprobante(pto_venta, cbte_tipo, numero)
    except (TransportError, AuthError, WsfeError) as exc:
        return Conciliacion(Estado.INCIERTO, numero, error=exc)

    if not consulta.existe:
        return Conciliacion(
            Estado.INCIERTO,
            numero,
            consulta,
            error=WsfeUncertain(
                "ARCA respondió 602: no encontró el comprobante en esta consulta.",
                detail=(
                    "Una respuesta 602 no garantiza que una autorización reciente "
                    "no vaya a aparecer después. No reenvíes el intento; conciliá "
                    "nuevamente o verificá el número en ARCA."
                ),
            ),
        )

    if consulta.resultado == "A" and consulta.cae:
        diferencias = _diferencias_con_solicitud(consulta, solicitud_esperada)
        if diferencias:
            return Conciliacion(
                Estado.INCIERTO,
                numero,
                consulta,
                error=WsfeUncertain(
                    "ARCA encontró un comprobante autorizado, pero sus datos no "
                    "coinciden con el intento guardado.",
                    detail="Diferencias: " + ", ".join(diferencias),
                ),
            )
        return Conciliacion(Estado.AUTORIZADO, numero, consulta, cae=consulta.cae)

    return Conciliacion(Estado.INCIERTO, numero, consulta)


def _diferencias_con_solicitud(
    consulta: ConsultaComprobante, solicitud: CaeRequest
) -> list[str]:
    diferencias: list[str] = []
    identificadores = (
        ("Concepto", consulta.concepto, CONCEPTO_PRODUCTOS),
        ("tipo de documento", consulta.doc_tipo, solicitud.doc_tipo),
        ("número de documento", consulta.doc_nro, solicitud.doc_nro),
        ("número desde", consulta.cbte_desde, solicitud.cbte_desde),
        ("número hasta", consulta.cbte_hasta, solicitud.cbte_desde),
        ("fecha", consulta.cbte_fch, solicitud.cbte_fch),
        (
            "condición IVA",
            consulta.condicion_iva_receptor,
            int(solicitud.condicion_iva_receptor),
        ),
        ("punto de venta", consulta.pto_venta, solicitud.pto_venta),
        ("tipo de comprobante", consulta.cbte_tipo, solicitud.cbte_tipo),
        ("moneda", consulta.mon_id, solicitud.mon_id),
    )
    diferencias.extend(
        nombre for nombre, actual, esperado in identificadores if actual != esperado
    )

    importes = (
        ("total", consulta.imp_total, solicitud.imp_total),
        ("no gravado", consulta.imp_tot_conc, solicitud.imp_tot_conc),
        ("neto", consulta.imp_neto, solicitud.imp_neto),
        ("exento", consulta.imp_op_ex, solicitud.imp_op_ex),
        ("tributos", consulta.imp_trib, solicitud.imp_trib),
        ("IVA", consulta.imp_iva, solicitud.imp_iva),
        ("cotización", consulta.mon_cotiz, solicitud.mon_cotiz),
    )
    diferencias.extend(
        nombre
        for nombre, actual, esperado in importes
        if not _mismo_numero(actual, esperado)
    )

    esperado_iva = sorted(
        [
            (alicuota.id, alicuota.base_imp, alicuota.importe)
            for alicuota in solicitud.alicuotas
        ],
        key=lambda row: (row[0] is None, row[0] or 0),
    )
    actual_iva = sorted(
        [
            (alicuota.id, alicuota.base_imp, alicuota.importe)
            for alicuota in consulta.alicuotas
        ],
        key=lambda row: (row[0] is None, row[0] or 0),
    )
    if len(actual_iva) != len(esperado_iva) or any(
        actual_id != expected_id
        or not _mismo_numero(actual_base, expected_base)
        or not _mismo_numero(actual_importe, expected_importe)
        for (actual_id, actual_base, actual_importe), (
            expected_id,
            expected_base,
            expected_importe,
        ) in zip(actual_iva, esperado_iva)
    ):
        diferencias.append("alícuotas de IVA")

    return diferencias


def _mismo_numero(actual: float | None, esperado: float) -> bool:
    if actual is None:
        return False
    try:
        return Decimal(str(actual)) == Decimal(str(esperado))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _centavos(valor: Decimal) -> float:
    """Quantise to cents before the float the closed core formats with ``.2f``."""
    return float(Decimal(valor).quantize(CENTAVO))


def _coincide(calculado: Decimal, informado: Decimal) -> bool:
    diferencia = abs(calculado - informado)
    return diferencia <= max(TOLERANCIA_ABSOLUTA, abs(informado) * TOLERANCIA_RELATIVA)
