from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from arca_integration.core.errors import AuthError
from arca_integration.core.wsaa import (
    build_login_cms_envelope,
    build_login_ticket_request,
    certificate_cuit,
    parse_login_ticket_response,
    sign_cms,
    validate_certificate_cuit,
    validate_key_pair,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _certificado(tmp_path, *, serial_number=None, common_name="ERP"):
    """A synthetic certificate. It never leaves the test."""
    subject = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    if serial_number is not None:
        subject.append(x509.NameAttribute(NameOID.SERIAL_NUMBER, serial_number))

    name = x509.Name(subject)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2028, 1, 1, tzinfo=timezone.utc))
        .sign(key, hashes.SHA256())
    )

    cert_path = tmp_path / "erp.crt"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "erp.key"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


def test_cuit_del_certificado_reads_the_serial_number(tmp_path):
    cert_path, _ = _certificado(tmp_path, serial_number="CUIT 20000000001")

    assert certificate_cuit(cert_path) == "20000000001"


def test_cuit_del_certificado_tolerates_separators(tmp_path):
    cert_path, _ = _certificado(tmp_path, serial_number="CUIT 20-00000000-1")

    assert certificate_cuit(cert_path) == "20000000001"


def test_a_certificate_without_a_cuit_is_rejected(tmp_path):
    cert_path, _ = _certificado(tmp_path)

    with pytest.raises(AuthError) as excinfo:
        certificate_cuit(cert_path)

    assert "CUIT" in excinfo.value.message


def test_validar_identidad_accepts_the_same_cuit_in_any_format(tmp_path):
    cert_path, _ = _certificado(tmp_path, serial_number="CUIT 20000000001")

    assert validate_certificate_cuit(cert_path, "20-00000000-1") == "20000000001"


def test_validar_identidad_rejects_another_cuit(tmp_path):
    cert_path, _ = _certificado(tmp_path, serial_number="CUIT 20000000001")

    with pytest.raises(AuthError) as excinfo:
        validate_certificate_cuit(cert_path, "20111111112")

    assert "no corresponde" in excinfo.value.message


def test_private_key_must_match_the_certificate(tmp_path):
    cert_path, key_path = _certificado(
        tmp_path, serial_number="CUIT 20000000001"
    )

    validate_key_pair(cert_path, Path(key_path).read_bytes())

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with pytest.raises(AuthError) as excinfo:
        validate_key_pair(cert_path, other_pem)

    assert "no corresponde" in excinfo.value.message


def test_sign_cms_checks_the_identity_when_a_cuit_is_given(tmp_path):
    cert_path, key_path = _certificado(tmp_path, serial_number="CUIT 20000000001")

    with pytest.raises(AuthError):
        sign_cms("<x/>", cert_path, key_path, represented_cuit="20111111112")

    assert sign_cms("<x/>", cert_path, key_path, represented_cuit="20000000001")


def test_build_login_ticket_request_uses_service_and_times():
    xml = build_login_ticket_request(
        "ws_sr_constancia_inscripcion",
        1,
        datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 11, 11, 20, tzinfo=timezone.utc),
    )

    assert "<service>ws_sr_constancia_inscripcion</service>" in xml
    assert "<uniqueId>1</uniqueId>" in xml
    assert "2026-11-11T11:00:00" in xml
    assert "2026-11-11T11:20:00" in xml


def test_parse_unescapes_login_cms_return():
    ticket = parse_login_ticket_response((FIXTURES / "wsaa_response.xml").read_bytes())

    assert ticket.token == "RkFLRVRPS0VORk9SUlRIRVRFU1Q="
    assert ticket.sign == "RkFLRVNJR05GT1JUSEVURVNU"
    assert ticket.expires_at.year == 2026
    assert ticket.expires_at.utcoffset().total_seconds() == -3 * 3600


def test_parse_reports_soap_fault_as_auth_error():
    with pytest.raises(AuthError) as excinfo:
        parse_login_ticket_response((FIXTURES / "wsaa_fault.xml").read_bytes())

    assert excinfo.value.message == "WSAA rechazó la autenticación."
    assert "coe.notAuthorized" in excinfo.value.detail


def test_sign_cms_reports_unreadable_credentials(tmp_path):
    with pytest.raises(AuthError):
        sign_cms("<x/>", str(tmp_path / "missing.crt"), str(tmp_path / "missing.key"))


def test_build_login_cms_envelope_embeds_base64():
    envelope = build_login_cms_envelope(b"der-bytes")

    assert b"ZGVyLWJ5dGVz" in envelope
    assert b"loginCms" in envelope
