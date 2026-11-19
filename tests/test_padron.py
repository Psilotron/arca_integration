from datetime import datetime, timezone
from pathlib import Path

import pytest

from arca_integration.core.errors import ConstanciaError, PersonaNotFound
from arca_integration.core.padron import (
    build_get_persona_envelope,
    normalize_cuit,
    parse_persona_response,
)
from arca_integration.core.wsaa import Ticket

FIXTURES = Path(__file__).parent / "fixtures"
TICKET = Ticket("tok", "sig", datetime(2026, 11, 11, 23, 15, tzinfo=timezone.utc))


def test_normalize_cuit_strips_formatting():
    assert normalize_cuit("30-50000173-5") == "30500001735"
    assert normalize_cuit(" 30500001735 ") == "30500001735"


def test_normalize_cuit_rejects_wrong_length():
    with pytest.raises(ValueError):
        normalize_cuit("123")


def test_normalize_cuit_rejects_letters():
    with pytest.raises(ValueError):
        normalize_cuit("30-5000-173X")


def test_envelope_carries_flat_auth_parameters():
    envelope = build_get_persona_envelope(TICKET, "20000000001", "30500001735").decode()
    body = envelope.split("<per:getPersona>")[1]

    assert "<sign>sig</sign>" in body
    assert "<token>tok</token>" in body
    assert "<cuitRepresentada>20000000001</cuitRepresentada>" in body
    assert "<idPersona>30500001735</idPersona>" in body
    assert "authRequest" not in envelope
    assert 'xmlns:per="http://a5.soap.ws.server.puc.sr/"' in envelope


def test_parses_juridica():
    persona = parse_persona_response((FIXTURES / "persona_juridica.xml").read_bytes())

    assert persona.cuit == "30500001735"
    assert persona.tipo_persona == "JURIDICA"
    assert persona.razon_social == "BANCO PRUEBA 02"
    assert persona.estado_clave == "ACTIVO"
    assert persona.domicilio["calle"] == "AV. CORRIENTES"
    assert persona.impuestos[0]["descripcionImpuesto"] == "IVA"
    assert persona.actividades[0]["periodo"] == "2026"
    assert persona.error_constancia is None


def test_parses_fisica_without_razon_social():
    persona = parse_persona_response((FIXTURES / "persona_fisica.xml").read_bytes())

    assert persona.tipo_persona == "FISICA"
    assert persona.nombre == "ELISA"
    assert persona.apellido == "DELIA BEATRIZ"
    assert persona.razon_social is None
    assert persona.estado_clave == "ACTIVO"


def test_parses_error_constancia():
    persona = parse_persona_response((FIXTURES / "persona_error.xml").read_bytes())

    assert persona.error_constancia is not None
    assert "biométricos" in persona.error_constancia
    assert persona.razon_social is None


def test_parses_fault():
    with pytest.raises(PersonaNotFound) as excinfo:
        parse_persona_response((FIXTURES / "persona_fault.xml").read_bytes())

    assert "No existe persona" in excinfo.value.detail


def test_unexpected_body_is_a_constancia_error():
    with pytest.raises(ConstanciaError):
        parse_persona_response(b"<soap:Envelope xmlns:soap='x'><soap:Body/></soap:Envelope>")
