"""WSAA (Web Service de Autenticación y Autorización) client.

ARCA authenticates callers with a short-lived ticket (TA). The flow is:
build a LoginTicketRequest, sign it as CMS/PKCS#7 with the taxpayer
certificate, post it, and keep the returned token + sign pair until it
expires (12 hours).
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

from .errors import AuthError

LOGIN_TICKET_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<loginTicketRequest version="1.0"><header>'
    "<uniqueId>{unique_id}</uniqueId>"
    "<generationTime>{generation_time}</generationTime>"
    "<expirationTime>{expiration_time}</expirationTime>"
    "</header><service>{service}</service></loginTicketRequest>"
)

LOGIN_CMS_ENVELOPE_TEMPLATE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov">'
    "<soapenv:Header/><soapenv:Body><wsaa:loginCms>"
    "<wsaa:in0>{cms_b64}</wsaa:in0>"
    "</wsaa:loginCms></soapenv:Body></soapenv:Envelope>"
)


@dataclass(frozen=True)
class Ticket:
    token: str
    sign: str
    expires_at: datetime


def build_login_ticket_request(
    service: str,
    unique_id: int,
    generation_time: datetime,
    expiration_time: datetime,
) -> str:
    return LOGIN_TICKET_TEMPLATE.format(
        unique_id=unique_id,
        generation_time=generation_time.isoformat(timespec="seconds"),
        expiration_time=expiration_time.isoformat(timespec="seconds"),
        service=service,
    )


def _load_certificate(cert_path: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    except (OSError, ValueError) as exc:
        raise AuthError(
            "No se pudo leer el certificado ARCA.",
            detail=f"{cert_path}: {exc}",
        ) from exc


# ARCA issues the certificate with the taxpayer CUIT in the subject, as
# ``SERIALNUMBER=CUIT 20xxxxxxxxx``. Separators vary, so the digits are extracted.
_CUIT_IN_SUBJECT = re.compile(r"CUIT[^0-9]*((?:[0-9][-\s]*){11})", re.IGNORECASE)


def certificate_cuit(cert_path: str) -> str:
    """Return the CUIT the certificate was issued to.

    Every subject attribute is inspected: ARCA puts it in ``SERIALNUMBER``, but
    a certificate reissued by another authority may carry it in ``OU``.
    """
    certificate = _load_certificate(cert_path)

    for attribute in certificate.subject:
        value = attribute.value
        if not isinstance(value, str):
            continue
        match = _CUIT_IN_SUBJECT.search(value)
        if match:
            return re.sub(r"[^0-9]", "", match.group(1))

    raise AuthError(
        "El certificado ARCA no declara el CUIT del contribuyente.",
        detail=certificate.subject.rfc4514_string(),
    )


def validate_certificate_cuit(cert_path: str, represented_cuit: str) -> str:
    """Fail unless the certificate belongs to the configured CUIT.

    WSAA rejects a ticket whose header CUIT does not match the signer, so the
    mismatch is caught before reaching the network.
    """
    certificate_cuit_value = certificate_cuit(cert_path)
    expected_cuit = re.sub(r"[^0-9]", "", str(represented_cuit or ""))

    if len(expected_cuit) != 11:
        raise AuthError(
            "El CUIT configurado para el emisor no es válido.",
            detail=f"configurado={represented_cuit!r}",
        )

    if certificate_cuit_value != expected_cuit:
        raise AuthError(
            "El CUIT del certificado no corresponde al CUIT configurado.",
            detail=f"certificado={certificate_cuit_value} configurado={expected_cuit}",
        )

    return certificate_cuit_value


def validate_key_pair(cert_path: str, key_data: bytes) -> None:
    """Fail unless the private key has the public key in the ARCA certificate."""
    certificate = _load_certificate(cert_path)
    private_key = load_private_key_data(key_data, source=cert_path)
    formato = serialization.PublicFormat.SubjectPublicKeyInfo
    certificado_publico = certificate.public_key().public_bytes(
        serialization.Encoding.DER, formato
    )
    clave_publica = private_key.public_key().public_bytes(
        serialization.Encoding.DER, formato
    )

    if certificado_publico != clave_publica:
        raise AuthError(
            "La clave privada no corresponde al certificado ARCA.",
            detail=cert_path,
        )


def load_private_key_data(data: bytes, source: str = ""):
    """Parse private-key bytes in PEM or OpenSSH format.

    This is the single place that decides which formats are accepted, so a key
    that saves in the settings is a key the WSAA client can sign with.
    """
    try:
        return serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError):
        pass

    try:
        return serialization.load_ssh_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise AuthError(
            "La clave privada ARCA no está en un formato soportado.",
            detail=f"{source}: {exc}" if source else str(exc),
        ) from exc


def load_private_key(key_path: str):
    try:
        data = Path(key_path).read_bytes()
    except OSError as exc:
        raise AuthError(
            "No se pudo leer la clave privada ARCA.",
            detail=f"{key_path}: {exc}",
        ) from exc

    return load_private_key_data(data, source=key_path)


def sign_cms(
    xml: str,
    cert_path: str,
    key_path: str,
    represented_cuit: str | None = None,
) -> bytes:
    if represented_cuit is not None:
        validate_certificate_cuit(cert_path, represented_cuit)

    certificate = _load_certificate(cert_path)
    private_key = load_private_key(key_path)

    try:
        return (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(xml.encode("utf-8"))
            .add_signer(certificate, private_key, hashes.SHA256())
            .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
        )
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise AuthError("No se pudo firmar el pedido de autenticación.", detail=str(exc)) from exc


def build_login_cms_envelope(cms_der: bytes) -> bytes:
    cms_b64 = base64.b64encode(cms_der).decode("ascii")
    return LOGIN_CMS_ENVELOPE_TEMPLATE.format(cms_b64=cms_b64).encode("utf-8")


def parse_login_ticket_response(body: bytes) -> Ticket:
    text = html.unescape(body.decode("utf-8", errors="replace"))

    fault = re.search(r"<faultstring>(.*?)</faultstring>", text, re.S)
    if fault:
        raise AuthError("WSAA rechazó la autenticación.", detail=fault.group(1).strip())

    inner = re.search(r"<loginCmsReturn>(.*?)</loginCmsReturn>", text, re.S)
    payload = inner.group(1) if inner else text

    token = re.search(r"<token>(.*?)</token>", payload, re.S)
    sign = re.search(r"<sign>(.*?)</sign>", payload, re.S)
    expiration = re.search(r"<expirationTime>(.*?)</expirationTime>", payload, re.S)

    if not (token and sign and expiration):
        raise AuthError(
            "La respuesta de WSAA no trae credenciales.",
            detail=text[:500],
        )

    try:
        expires_at = datetime.fromisoformat(expiration.group(1).strip())
    except ValueError as exc:
        raise AuthError("La respuesta de WSAA trae un vencimiento inválido.", detail=str(exc)) from exc

    return Ticket(token=token.group(1).strip(), sign=sign.group(1).strip(), expires_at=expires_at)
