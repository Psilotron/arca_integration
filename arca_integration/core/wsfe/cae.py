"""Requesting a CAE and classifying ARCA's answer.

ARCA answers with a header ``Resultado`` that is one of three letters:

``A``
    Authorized. The detail carries the ``CAE`` and its expiry.
``R``
    Rejected. No comprobante exists, so asking again changes nothing.
``P``
    Partial. The comprobante may or may not exist; ARCA is telling us it could
    not settle the request. Guessing is never allowed: reconcile with the
    comprobante query before issuing again.

The rejection reason arrives in two different places depending on where the
validation failed: a header-level failure lands in ``Errors.Err``, while a
detail-level failure lands in ``Observaciones.Obs``. Both were captured live on
2026-11-12 and both are read here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import WsfeRejected, WsfeUncertain
from .types import Cae, CondicionIvaReceptor
from .xml import (
    Auth,
    auth_xml,
    build_envelope,
    find,
    find_all,
    first_error,
    parse_root,
    text_of,
)

CONCEPTO_PRODUCTOS = 1


@dataclass(frozen=True)
class AlicIva:
    """One VAT rate line: the catalog id, its taxable base and its amount."""

    id: int
    base_imp: float
    importe: float


@dataclass(frozen=True)
class CaeRequest:
    """One comprobante to authorize.

    ``cbte_desde`` is both the first and the last number of the range, because
    every request carries exactly one comprobante. ``doc_tipo=99`` with
    ``doc_nro=0`` is ARCA's "unidentified final consumer".

    ``alicuotas`` must add up: ``imp_neto`` equals the sum of their bases and
    ``imp_iva`` the sum of their amounts, and ``imp_total`` equals the sum of
    every component. ARCA rejects the mismatch with ``10048``.
    """

    pto_venta: int
    cbte_tipo: int
    cbte_desde: int
    cbte_fch: str
    doc_tipo: int
    doc_nro: int
    imp_total: float
    imp_neto: float
    imp_iva: float
    condicion_iva_receptor: CondicionIvaReceptor
    alicuotas: list[AlicIva] = field(default_factory=list)
    mon_id: str = "PES"
    mon_cotiz: float = 1.0
    imp_tot_conc: float = 0.0
    imp_op_ex: float = 0.0
    imp_trib: float = 0.0


def _amount(value: float) -> str:
    return f"{value:.2f}"


def build_cae_request(auth: Auth, request: CaeRequest) -> bytes:
    """Envelope for ``FECAESolicitar``.

    Field order is ARCA's, not ours: SOAP 1.1 document/literal rejects nothing,
    it silently misreads. The service dates are omitted on purpose, because
    ``Concepto`` is always products here; they become mandatory for ``2`` or
    ``3`` and must be added together with that change.
    """
    cabecera = (
        "<ar:FeCabReq>"
        "<ar:CantReg>1</ar:CantReg>"
        f"<ar:PtoVta>{request.pto_venta}</ar:PtoVta>"
        f"<ar:CbteTipo>{request.cbte_tipo}</ar:CbteTipo>"
        "</ar:FeCabReq>"
    )
    alicuotas = "".join(
        "<ar:AlicIva>"
        f"<ar:Id>{alicuota.id}</ar:Id>"
        f"<ar:BaseImp>{_amount(alicuota.base_imp)}</ar:BaseImp>"
        f"<ar:Importe>{_amount(alicuota.importe)}</ar:Importe>"
        "</ar:AlicIva>"
        for alicuota in request.alicuotas
    )
    detalle = (
        "<ar:FeDetReq><ar:FECAEDetRequest>"
        f"<ar:Concepto>{CONCEPTO_PRODUCTOS}</ar:Concepto>"
        f"<ar:DocTipo>{request.doc_tipo}</ar:DocTipo>"
        f"<ar:DocNro>{request.doc_nro}</ar:DocNro>"
        f"<ar:CbteDesde>{request.cbte_desde}</ar:CbteDesde>"
        f"<ar:CbteHasta>{request.cbte_desde}</ar:CbteHasta>"
        f"<ar:CbteFch>{request.cbte_fch}</ar:CbteFch>"
        f"<ar:ImpTotal>{_amount(request.imp_total)}</ar:ImpTotal>"
        f"<ar:ImpTotConc>{_amount(request.imp_tot_conc)}</ar:ImpTotConc>"
        f"<ar:ImpNeto>{_amount(request.imp_neto)}</ar:ImpNeto>"
        f"<ar:ImpOpEx>{_amount(request.imp_op_ex)}</ar:ImpOpEx>"
        f"<ar:ImpTrib>{_amount(request.imp_trib)}</ar:ImpTrib>"
        f"<ar:ImpIVA>{_amount(request.imp_iva)}</ar:ImpIVA>"
        f"<ar:MonId>{request.mon_id}</ar:MonId>"
        f"<ar:MonCotiz>{_amount(request.mon_cotiz)}</ar:MonCotiz>"
        f"<ar:CondicionIVAReceptorId>{int(request.condicion_iva_receptor)}"
        "</ar:CondicionIVAReceptorId>"
        f"<ar:Iva>{alicuotas}</ar:Iva>"
        "</ar:FECAEDetRequest></ar:FeDetReq>"
    )
    inner = auth_xml(auth) + f"<ar:FeCAEReq>{cabecera}{detalle}</ar:FeCAEReq>"
    return build_envelope("FECAESolicitar", inner)


def parse_cae_response(body: bytes) -> Cae:
    """Classify ARCA's answer, or raise when it is not an authorization.

    Returns ``Cae`` only for ``Resultado: A``; every other answer raises, so a
    caller can never mistake a rejection or an unknown state for a CAE.
    """
    root = parse_root(body)
    cabecera = find(root, "FeCabResp")
    resultado = text_of(cabecera, "Resultado")
    reproceso = text_of(cabecera, "Reproceso")

    if resultado == "A":
        return _approved(root, reproceso)
    if resultado == "R":
        code, message = _rejection(root)
        raise WsfeRejected(message, code=code)

    raise WsfeUncertain(
        "ARCA no confirmó ni rechazó el comprobante.",
        code=None,
        detail=f"Resultado: {resultado or 'ausente'}",
    )


def _approved(root, reproceso: str | None) -> Cae:
    detalles = find_all(root, "FECAEDetResponse")
    if not detalles:
        raise WsfeUncertain(
            "ARCA aprobó el comprobante sin devolver el detalle.",
            detail="Falta FeDetResp.FECAEDetResponse",
        )

    cae = text_of(detalles[0], "CAE")
    vence = text_of(detalles[0], "CAEFchVto")
    if not cae or not vence:
        raise WsfeUncertain(
            "ARCA aprobó el comprobante sin devolver el CAE.",
            detail="Falta CAE o CAEFchVto",
        )

    return Cae(cae=cae, vence=vence, resultado="A", reproceso=reproceso)


def _rejection(root) -> tuple[int | None, str]:
    """First rejection reason, from ``Errors`` or from ``Observaciones``."""
    error = first_error(root)
    if error is not None:
        return error

    observaciones = find(root, "Observaciones")
    if observaciones is not None:
        for obs in find_all(observaciones, "Obs"):
            message = text_of(obs, "Msg") or "ARCA rechazó el comprobante sin indicar el motivo."
            raw_code = text_of(obs, "Code")
            try:
                return (int(raw_code) if raw_code else None), message
            except ValueError:
                return None, message

    return None, "ARCA rechazó el comprobante sin indicar el motivo."
