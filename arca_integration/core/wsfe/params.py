"""ARCA's parameter catalogs: what exists and what may be used.

An empty catalog is raised, never rendered as an empty list: ARCA reports it as
an error body (``Err 602 "Sin Resultados"``), and swallowing that into ``[]``
would hide the reason.

``FEParamGetPtosVenta`` is **not** a health check. In homologación it answers
``602`` even when issuing from point of sale 1 works; measured here on
2026-11-12. Never conclude from it that a point of sale is missing.
"""

from __future__ import annotations

from ..errors import WsfeError
from .types import ComprobanteType, PuntoVenta
from .xml import Auth, auth_xml, build_envelope, find_all, first_error, parse_root, text_of

_POINT_OF_SALE = "PtoVenta"
_COMPROBANTE_TYPE = "CbteTipo"


def build_param_request(operation: str, auth: Auth) -> bytes:
    """Envelope for a parameter operation, which takes only ``Auth``."""
    return build_envelope(operation, auth_xml(auth))


def parse_ptos_venta(body: bytes) -> list[PuntoVenta]:
    rows = _read_catalog(body, _POINT_OF_SALE, "ARCA no devolvió puntos de venta.")

    return [
        PuntoVenta(
            nro=int(text_of(row, "Nro") or 0),
            emision_tipo=text_of(row, "EmisionTipo") or "",
            bloqueado=(text_of(row, "Bloqueado") or "N").upper() == "S",
            fch_baja=_open_window(text_of(row, "FchBaja")),
        )
        for row in rows
    ]


def parse_tipos_cbte(body: bytes) -> list[ComprobanteType]:
    rows = _read_catalog(body, _COMPROBANTE_TYPE, "ARCA no devolvió tipos de comprobante.")

    return [
        ComprobanteType(
            id=int(text_of(row, "Id") or 0),
            desc=text_of(row, "Desc") or "",
            fch_desde=text_of(row, "FchDesde"),
            fch_hasta=_open_window(text_of(row, "FchHasta")),
        )
        for row in rows
    ]


def _read_catalog(body: bytes, item: str, message: str) -> list:
    root = parse_root(body)
    error = first_error(root)
    if error is not None:
        code, detail = error
        raise WsfeError(message, code=code, detail=detail)

    rows = find_all(root, item)
    if not rows:
        raise WsfeError(message)
    return rows


def _open_window(value: str | None) -> str | None:
    """ARCA answers the literal ``NULL`` for an open validity window."""
    if value is None or value.upper() == "NULL":
        return None
    return value
