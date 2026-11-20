from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from frappe.tests import UnitTestCase

from arca_integration.services.credentials import (
	InvalidCredential,
	build_csr,
	generate_private_key,
	is_valid_cuit,
	key_matches_certificate,
	normalize_pem,
	read_certificate,
)

CUIT = "30000000007"  # sample CUIT used by ARCA in its QR specification


def make_certificate(private_key_pem: str, cuit: str = CUIT, days: int = 730) -> str:
	"""Self-signed certificate standing in for the one ARCA issues."""
	key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
	name = x509.Name(
		[
			x509.NameAttribute(NameOID.COMMON_NAME, "test"),
			x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
		]
	)
	now = datetime.now(UTC)
	cert = (
		x509.CertificateBuilder()
		.subject_name(name)
		.issuer_name(name)
		.public_key(key.public_key())
		.serial_number(x509.random_serial_number())
		.not_valid_before(now - timedelta(days=1))
		.not_valid_after(now + timedelta(days=days))
		.sign(key, hashes.SHA256())
	)
	return cert.public_bytes(serialization.Encoding.PEM).decode()


class TestCuit(UnitTestCase):
	def test_valid(self):
		for cuit in ("30000000007", "20000000001", "20-00000000-1", "30 00000000 7"):
			self.assertTrue(is_valid_cuit(cuit), cuit)

	def test_invalid(self):
		for cuit in ("30000000008", "3000000000", "abc", "", "300000000071"):
			self.assertFalse(is_valid_cuit(cuit), cuit)


class TestCredentials(UnitTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.key = generate_private_key()
		cls.certificate = make_certificate(cls.key)

	def test_csr_subject(self):
		csr = x509.load_pem_x509_csr(build_csr(self.key, CUIT, "ACME SA", "erp-testing").encode())
		self.assertTrue(csr.is_signature_valid)
		self.assertEqual(
			csr.subject.rfc4514_string({NameOID.SERIAL_NUMBER: "serialNumber"}),
			"serialNumber=CUIT 30000000007,CN=erp-testing,O=ACME SA,C=AR",
		)

	def test_csr_rejects_invalid_cuit(self):
		with self.assertRaises(InvalidCredential):
			build_csr(self.key, "30000000008", "ACME SA", "erp-testing")

	def test_read_certificate(self):
		info = read_certificate(self.certificate)
		self.assertEqual(info.cuit, CUIT)
		self.assertEqual(info.subject, "serialNumber=CUIT 30000000007,CN=test")
		self.assertGreater(info.not_after, info.not_before)

	def test_key_matches_certificate(self):
		self.assertTrue(key_matches_certificate(self.key, self.certificate))
		self.assertFalse(key_matches_certificate(generate_private_key(), self.certificate))

	def test_pem_without_line_breaks_is_rebuilt(self):
		flattened = self.key.replace("\n", " ")
		self.assertEqual(normalize_pem(flattened), self.key)
		self.assertTrue(key_matches_certificate(flattened, self.certificate))

	def test_garbage_is_rejected(self):
		with self.assertRaises(InvalidCredential):
			read_certificate("not a certificate")
		with self.assertRaises(InvalidCredential):
			key_matches_certificate(
				"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----", self.certificate
			)
