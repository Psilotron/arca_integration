"""CUIT validation and X.509 credentials for WSAA.

The private key is generated here, on the server, and only the CSR leaves it.
ARCA signs the CSR ("Administración de Certificados Digitales" in production,
WSASS in homologation) and the returned certificate is pasted back into the app.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

KEY_SIZE = 2048
_CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_SUBJECT_NAMES = {NameOID.SERIAL_NUMBER: "serialNumber"}
_PEM_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.DOTALL)


class InvalidCredential(ValueError):
	"""A certificate or private key cannot be parsed or does not fit."""


@dataclass(frozen=True)
class Credentials:
	"""Everything WSAA needs to sign a login request for one company and environment."""

	cuit: str
	certificate: str
	private_key: str


@dataclass(frozen=True)
class CertificateInfo:
	subject: str
	cuit: str | None
	not_before: datetime
	not_after: datetime


def normalize_cuit(value: str) -> str:
	"""Strip separators: '20-12345678-6' -> '20123456786'."""
	return re.sub(r"[\s.-]", "", value or "")


def is_valid_cuit(value: str) -> bool:
	"""Check length and the modulo 11 check digit of a CUIT/CUIL."""
	cuit = normalize_cuit(value)
	if not re.fullmatch(r"\d{11}", cuit):
		return False

	remainder = sum(int(d) * w for d, w in zip(cuit[:10], _CUIT_WEIGHTS, strict=True)) % 11
	check = 11 - remainder
	if check == 11:
		check = 0
	if check == 10:
		return False
	return check == int(cuit[10])


def normalize_pem(value: str) -> str:
	"""Rebuild a PEM block whose line breaks were lost, e.g. when pasted into a one-line input."""
	match = _PEM_RE.search(value or "")
	if not match:
		raise InvalidCredential("Not a PEM block")

	label, body = match.group(1), re.sub(r"\s", "", match.group(2))
	lines = [body[i : i + 64] for i in range(0, len(body), 64)]
	return "\n".join([f"-----BEGIN {label}-----", *lines, f"-----END {label}-----", ""])


def generate_private_key() -> str:
	"""Return a new unencrypted RSA private key in PKCS#8 PEM."""
	key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
	return key.private_bytes(
		serialization.Encoding.PEM,
		serialization.PrivateFormat.PKCS8,
		serialization.NoEncryption(),
	).decode()


def validate_private_key(private_key_pem: str) -> str:
	"""Return the key as normalized PEM, or raise InvalidCredential."""
	_load_private_key(private_key_pem)
	return normalize_pem(private_key_pem)


def build_csr(private_key_pem: str, cuit: str, organization: str, common_name: str) -> str:
	"""Return a CSR with the subject ARCA requires: C=AR, O, CN and serialNumber='CUIT <cuit>'."""
	cuit = normalize_cuit(cuit)
	if not is_valid_cuit(cuit):
		raise InvalidCredential(f"Invalid CUIT: {cuit}")

	subject = x509.Name(
		[
			x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
			x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
			x509.NameAttribute(NameOID.COMMON_NAME, common_name),
			x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
		]
	)
	csr = (
		x509.CertificateSigningRequestBuilder()
		.subject_name(subject)
		.sign(_load_private_key(private_key_pem), hashes.SHA256())
	)
	return csr.public_bytes(serialization.Encoding.PEM).decode()


def read_certificate(certificate_pem: str) -> CertificateInfo:
	"""Parse a PEM certificate and return what the settings form shows."""
	cert = _load_certificate(certificate_pem)
	serials = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
	cuit = None
	if serials and (match := re.search(r"\d{11}", str(serials[0].value))):
		cuit = match.group(0)

	return CertificateInfo(
		subject=cert.subject.rfc4514_string(_SUBJECT_NAMES),
		cuit=cuit,
		not_before=cert.not_valid_before_utc,
		not_after=cert.not_valid_after_utc,
	)


def key_matches_certificate(private_key_pem: str, certificate_pem: str) -> bool:
	"""True when the certificate was issued for this private key."""
	public_from_key = _load_private_key(private_key_pem).public_key()
	public_from_cert = _load_certificate(certificate_pem).public_key()
	fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
	return public_from_key.public_bytes(*fmt) == public_from_cert.public_bytes(*fmt)


def _load_private_key(pem: str):
	try:
		return serialization.load_pem_private_key(normalize_pem(pem).encode(), password=None)
	except (ValueError, TypeError) as exc:
		raise InvalidCredential("Invalid or encrypted private key") from exc


def _load_certificate(pem: str) -> x509.Certificate:
	try:
		return x509.load_pem_x509_certificate(normalize_pem(pem).encode())
	except ValueError as exc:
		raise InvalidCredential("Invalid certificate") from exc
