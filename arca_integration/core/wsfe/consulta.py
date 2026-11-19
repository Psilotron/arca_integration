"""Reading a comprobante back from ARCA.

``FECompConsultar`` is the only way to answer "did that issuance happen?". It is
the exit from an uncertain emission, because ARCA's numbering cannot be replayed:
re-sending an authorized number answers ``10016`` instead of the existing CAE.

Absence was measured against homologación on 2026-11-12: ARCA answers ``Err 602``
—"No existen datos en nuestros registros para los parametros ingresados"— not an
empty ``ResultGet``. Only 602 is read as absence; any other error stays an error,
because concluding "not authorized" from an unreadable answer is how a consumed
number gets issued twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import WsfeError
from .xml import (
    Auth,
    as_number,
    auth_xml,
    build_envelope,
    find,
    find_all,
    first_error,
    parse_root,
    text_of,
)

NO_RECORDS = 602


@dataclass(frozen=True)
class ConsultaAlicIva:
    """One IVA row returned by ``FECompConsultar``."""

    id: int | None
    base_imp: float | None
    importe: float | None


@dataclass(frozen=True)
class ConsultaComprobante:
    """ARCA's record of one comprobante, or the fact that it has none.

    ``existe is False`` means ARCA had no matching record at query time. It is
    weaker than it looks: a number that was authorized but not yet visible would
    also answer 602. Callers may query again, but must not treat it as proof that
    reissuing the number is safe.

    ``raw`` carries the answer untouched, so the operator can see exactly what
    ARCA said when a reconciliation does not settle.
    """

    existe: bool
    resultado: str | None = None
    cae: str | None = None
    vence: str | None = None
    emision_tipo: str | None = None
    cbte_desde: int | None = None
    cbte_hasta: int | None = None
    imp_total: float | None = None
    concepto: int | None = None
    doc_tipo: int | None = None
    doc_nro: int | None = None
    cbte_fch: str | None = None
    imp_tot_conc: float | None = None
    imp_neto: float | None = None
    imp_op_ex: float | None = None
    imp_trib: float | None = None
    imp_iva: float | None = None
    mon_id: str | None = None
    mon_cotiz: float | None = None
    condicion_iva_receptor: int | None = None
    pto_venta: int | None = None
    cbte_tipo: int | None = None
    alicuotas: tuple[ConsultaAlicIva, ...] = ()
    raw: bytes = field(default=b"", repr=False, compare=False)


def build_consulta_request(
    auth: Auth, pto_venta: int, cbte_tipo: int, cbte_nro: int
) -> bytes:
    """Envelope for ``FECompConsultar``.

    Field order is ARCA's and it is not the order of the other operations:
    ``CbteTipo``, ``CbteNro``, ``PtoVta``.
    """
    inner = (
        auth_xml(auth)
        + "<ar:FeCompConsReq>"
        + f"<ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>"
        + f"<ar:CbteNro>{cbte_nro}</ar:CbteNro>"
        + f"<ar:PtoVta>{pto_venta}</ar:PtoVta>"
        + "</ar:FeCompConsReq>"
    )
    return build_envelope("FECompConsultar", inner)


def parse_consulta_response(body: bytes) -> ConsultaComprobante:
    """Classify ARCA's answer into a record, an absence, or an error."""
    root = parse_root(body)

    registro = find(root, "ResultGet")
    if registro is not None:
        iva = find(registro, "Iva")
        return ConsultaComprobante(
            existe=True,
            resultado=text_of(registro, "Resultado"),
            cae=text_of(registro, "CodAutorizacion"),
            vence=text_of(registro, "FchVto"),
            emision_tipo=text_of(registro, "EmisionTipo"),
            cbte_desde=as_number(text_of(registro, "CbteDesde")),
            cbte_hasta=as_number(text_of(registro, "CbteHasta")),
            imp_total=_as_float(text_of(registro, "ImpTotal")),
            concepto=as_number(text_of(registro, "Concepto")),
            doc_tipo=as_number(text_of(registro, "DocTipo")),
            doc_nro=as_number(text_of(registro, "DocNro")),
            cbte_fch=text_of(registro, "CbteFch"),
            imp_tot_conc=_as_float(text_of(registro, "ImpTotConc")),
            imp_neto=_as_float(text_of(registro, "ImpNeto")),
            imp_op_ex=_as_float(text_of(registro, "ImpOpEx")),
            imp_trib=_as_float(text_of(registro, "ImpTrib")),
            imp_iva=_as_float(text_of(registro, "ImpIVA")),
            mon_id=text_of(registro, "MonId"),
            mon_cotiz=_as_float(text_of(registro, "MonCotiz")),
            condicion_iva_receptor=as_number(
                text_of(registro, "CondicionIVAReceptorId")
            ),
            pto_venta=as_number(text_of(registro, "PtoVta")),
            cbte_tipo=as_number(text_of(registro, "CbteTipo")),
            alicuotas=tuple(
                ConsultaAlicIva(
                    id=as_number(text_of(item, "Id")),
                    base_imp=_as_float(text_of(item, "BaseImp")),
                    importe=_as_float(text_of(item, "Importe")),
                )
                for item in find_all(iva, "AlicIva")
            ) if iva is not None else (),
            raw=body,
        )

    error = first_error(root)
    if error is not None:
        code, message = error
        if code == NO_RECORDS:
            return ConsultaComprobante(existe=False, raw=body)
        raise WsfeError(message, code=code)

    raise WsfeError(
        "ARCA no devolvió ni el comprobante ni el motivo del rechazo.",
        detail=body.decode(errors="replace")[:500],
    )


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
