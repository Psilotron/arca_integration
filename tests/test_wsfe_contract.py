"""The frozen public surface of the WSFE engine.

Phase B builds a DocType on top of this module, so a rename here would break it
silently. Importing every name and checking the two signatures that matter pins
the surface without freezing the internals.
"""

from typing import get_type_hints


def test_every_public_name_is_importable():
    from arca_integration.core.wsfe import (  # noqa: F401
        WSFE_SERVICE,
        WSFE_URL,
        AlicIva,
        Cae,
        CaeRequest,
        ComprobanteType,
        CondicionIvaReceptor,
        ConsultaComprobante,
        PuntoVenta,
        WsfeClient,
        WsfeError,
        WsfeRejected,
        WsfeUncertain,
    )

    assert WSFE_SERVICE == "wsfe"


def test_the_client_returns_cae_objects():
    from arca_integration.core.wsfe import Cae, CaeRequest, WsfeClient

    hints = get_type_hints(WsfeClient.solicitar_cae)

    assert hints["return"] is Cae
    assert hints["request"] is CaeRequest


def test_the_client_can_read_a_comprobante_back():
    """Phase B's only exit from an uncertain issuance."""
    from arca_integration.core.wsfe import ConsultaComprobante, WsfeClient

    hints = get_type_hints(WsfeClient.consultar_comprobante)

    assert hints["return"] is ConsultaComprobante


def test_the_errors_share_one_base_so_callers_can_catch_them_together():
    from arca_integration.core.wsfe import WsfeError, WsfeRejected, WsfeUncertain

    assert issubclass(WsfeRejected, WsfeError)
    assert issubclass(WsfeUncertain, WsfeError)
