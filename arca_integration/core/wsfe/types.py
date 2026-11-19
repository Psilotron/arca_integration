"""Value objects returned by the WSFE catalogs and requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


@dataclass(frozen=True)
class PuntoVenta:
    """A point of sale assigned to electronic invoicing.

    ``emision_tipo`` is ARCA's mode (``CAE`` or ``CAEA``). ``bloqueado`` and
    ``fch_baja`` describe whether it can be used right now, so a caller never has
    to read the raw XML to know that.
    """

    nro: int
    emision_tipo: str
    bloqueado: bool
    fch_baja: str | None


@dataclass(frozen=True)
class ComprobanteType:
    """A comprobante type from ARCA's catalog, with its validity window.

    ``fch_hasta`` is ``None`` while the type is still valid: the catalog answers
    the literal string ``NULL`` for an open window.
    """

    id: int
    desc: str
    fch_desde: str | None
    fch_hasta: str | None


class CondicionIvaReceptor(IntEnum):
    """The receiver's VAT condition, as ARCA numbers it.

    ``CondicionIVAReceptorId`` is mandatory in homologación since 9 June 2025
    and must be an integer, not a label.

    The values and their descriptions come from ``FEParamGetCondicionIvaReceptor``,
    measured against homologación on 2026-11-12 — not from documentation.

    ARCA also answers ``Cmp_Clase`` for each condition, and that field is **not**
    used to refuse anything: it puts ``Consumidor Final`` (5) in ``C/49``, yet a
    Factura B with condition 5 was authorized here the day before. Until that
    disagreement is understood, the class pairing lives in
    ``arca_integration.core.comprobante.CONDICIONES_VERIFICADAS``, which encodes
    what was measured, and ARCA's ``10243`` remains the authority.
    """

    RESPONSABLE_INSCRIPTO = 1  # IVA Responsable Inscripto
    EXENTO = 4  # IVA Sujeto Exento
    CONSUMIDOR_FINAL = 5  # Consumidor Final
    MONOTRIBUTO = 6  # Responsable Monotributo
    SUJETO_NO_CATEGORIZADO = 7  # Sujeto No Categorizado
    PROVEEDOR_DEL_EXTERIOR = 8  # Proveedor del Exterior
    CLIENTE_DEL_EXTERIOR = 9  # Cliente del Exterior
    IVA_LIBERADO_LEY_19640 = 10  # IVA Liberado - Ley N. 19.640
    MONOTRIBUTISTA_SOCIAL = 13  # Monotributista Social
    IVA_NO_ALCANZADO = 15  # IVA No Alcanzado
    MONOTRIBUTO_TRABAJADOR_INDEPENDIENTE_PROMOVIDO = 16  # Monotributo Trabajador Independiente Promovido


@dataclass(frozen=True)
class Cae:
    """An authorized comprobante's code of authorization.

    ``reproceso`` is ``"S"`` when ARCA recognized a request it had already
    processed and answered with the existing CAE. That is not a fresh
    authorization, so callers that must distinguish the two read this field
    instead of assuming the answer is new.
    """

    cae: str
    vence: str
    resultado: str
    reproceso: str | None = None
