"""WSFE (``wsfev1``) fiscal engine.

The engine authenticates for the ``wsfe`` service, reads ARCA's catalogs and
numbering, and requests a CAE. It is pure Python: no Frappe import, and every
operation runs offline through an injected transport.
"""

from ..errors import WsfeError, WsfeRejected, WsfeUncertain
from .cae import AlicIva, CaeRequest
from .client import WSFE_SERVICE, WSFE_URL, WsfeClient
from .consulta import ConsultaComprobante
from .types import Cae, ComprobanteType, CondicionIvaReceptor, PuntoVenta

__all__ = [
    "WSFE_SERVICE",
    "WSFE_URL",
    "WsfeClient",
    "WsfeError",
    "WsfeRejected",
    "WsfeUncertain",
    "AlicIva",
    "Cae",
    "CaeRequest",
    "ComprobanteType",
    "CondicionIvaReceptor",
    "ConsultaComprobante",
    "PuntoVenta",
]
