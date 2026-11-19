"""Numbering: which comprobante number comes next for a point of sale.

The number is ARCA's, not ours. ``FECompUltimoAutorizado`` answers the last one
it authorized, and the next is that plus one. Answering ``0`` means nothing was
ever issued there, so the first number is ``1``.

A nonexistent point of sale also answers ``0`` without an error, so the number
alone never proves the point of sale exists. Neither does ``FEParamGetPtosVenta``,
which answers ``602`` in homologación even when issuing works. Only
``FECAESolicitar`` settles it: it validates the point of sale and answers
``10005`` when it is not enabled.
"""

from __future__ import annotations

from ..errors import WsfeRejected
from .xml import Auth, auth_xml, build_envelope, first_error, parse_root, text_of


def build_last_authorized_request(auth: Auth, pto_venta: int, cbte_tipo: int) -> bytes:
    inner = (
        auth_xml(auth)
        + f"<ar:PtoVta>{pto_venta}</ar:PtoVta>"
        + f"<ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>"
    )
    return build_envelope("FECompUltimoAutorizado", inner)


def parse_last_authorized(body: bytes) -> int:
    """The last authorized number, or ``WsfeRejected`` if ARCA refused."""
    root = parse_root(body)
    error = first_error(root)
    if error is not None:
        code, message = error
        raise WsfeRejected(message, code=code, detail=message)

    raw = text_of(root, "CbteNro")
    if raw is None:
        raise WsfeRejected(
            "ARCA no devolvió el último comprobante autorizado.",
            detail=body.decode(errors="replace")[:500],
        )
    return int(raw)


def next_voucher_number(last_authorized: int) -> int:
    """The number to request next: one after the last authorized one."""
    return last_authorized + 1
