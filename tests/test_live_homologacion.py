"""Opt-in integration test against ARCA homologación.

Manual verification of the same flow was done with curl + openssl before this
test existed; it pins the values observed there.
"""

import os

import pytest

from arca_integration.core.client import ArcaClient
from arca_integration.core.errors import ConstanciaError, PersonaNotFound
from arca_integration.core.ticket_cache import FileTicketCache

pytestmark = pytest.mark.live

LIVE = os.getenv("ARCA_LIVE") == "1"
# The CUIT of the certificate is a credential fact, never a default: the ticket
# is rejected if the header CUIT does not match the signer.
CUIT = os.environ.get("ARCA_CUIT")
TA_CACHE = os.environ.get("ARCA_TA_CACHE", "/tmp/arca-ta.json")


@pytest.fixture
def client():
    if not LIVE:
        pytest.skip("set ARCA_LIVE=1 to run against homologación")
    if not CUIT:
        pytest.skip("set ARCA_CUIT to the CUIT the certificate was issued to")
    return ArcaClient(
        wsaa_url=os.environ.get(
            "ARCA_WSAA_URL", "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
        ),
        padron_url=os.environ.get(
            "ARCA_PADRON_URL",
            "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
        ),
        cert_path=os.environ.get("ARCA_CERT", "tmp/erp.crt"),
        key_path=os.environ.get("ARCA_KEY", "tmp/erp.pem"),
        cuit_representada=CUIT,
        cache=FileTicketCache(TA_CACHE),
    )


def test_live_lookup_juridica(client):
    persona = client.lookup("30500001735")

    assert persona.razon_social == "BANCO PRUEBA 02"
    assert persona.estado_clave == "ACTIVO"
    assert persona.tipo_persona == "JURIDICA"


def test_live_lookup_fisica(client):
    persona = client.lookup("27222222228")

    assert persona.tipo_persona == "FISICA"
    assert persona.estado_clave == "ACTIVO"
    assert persona.razon_social is None


def test_live_constancia_error(client):
    with pytest.raises(ConstanciaError):
        client.lookup("20222222223")


def test_live_unknown_cuit(client):
    with pytest.raises(PersonaNotFound):
        client.lookup(client.cuit_representada)
