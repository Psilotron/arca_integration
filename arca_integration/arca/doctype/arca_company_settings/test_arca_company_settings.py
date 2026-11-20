# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

import json

import frappe
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from frappe.tests import IntegrationTestCase

from arca_integration.arca.doctype.arca_company_settings.arca_company_settings import generate_csr
from arca_integration.services.credentials import generate_private_key
from arca_integration.tests.test_credentials import make_certificate
from arca_integration.tests.utils import get_test_company

DOCTYPE = "ARCA Company Settings"

# Use the minimal company from tests.utils instead of ERPNext's full Company test records.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "Address"]


class TestARCACompanySettings(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()
		cls.key = generate_private_key()
		cls.certificate = make_certificate(cls.key)

	def setUp(self):
		frappe.delete_doc_if_exists(DOCTYPE, self.company, force=True)

	def make(self, **values):
		return frappe.get_doc({"doctype": DOCTYPE, "company": self.company, **values}).insert()

	def test_cuit_is_taken_from_company_and_normalized(self):
		doc = self.make()
		self.assertEqual(doc.cuit, "30000000007")
		self.assertEqual(doc.environment, "Homologation")

	def test_invalid_cuit_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make(cuit="30000000008")

	def test_certificate_details_are_filled(self):
		doc = self.make(homologation_private_key=self.key, homologation_certificate=self.certificate)
		self.assertEqual(doc.homologation_certificate_subject, "serialNumber=CUIT 30000000007,CN=test")
		self.assertIsNotNone(doc.homologation_certificate_expiry)

		credentials = doc.get_credentials("Homologation")
		self.assertEqual(credentials.private_key, self.key)
		self.assertEqual(credentials.cuit, "30000000007")

	def test_certificate_for_another_key_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make(
				homologation_private_key=generate_private_key(), homologation_certificate=self.certificate
			)

	def test_key_pasted_without_line_breaks_is_accepted(self):
		doc = self.make(
			homologation_private_key=self.key.replace("\n", " "), homologation_certificate=self.certificate
		)
		self.assertEqual(doc.get_private_key("Homologation"), self.key)

	def test_missing_credentials_raise(self):
		doc = self.make()
		with self.assertRaises(frappe.ValidationError):
			doc.get_credentials("Production")

	def test_private_key_never_leaves_the_server(self):
		self.make(homologation_private_key=self.key, homologation_certificate=self.certificate)
		frappe.clear_document_cache(DOCTYPE, self.company)

		stored_column = frappe.db.get_value(DOCTYPE, self.company, "homologation_private_key")
		self.assertNotIn("PRIVATE KEY", stored_column or "")

		as_client_sees_it = json.dumps(frappe.get_doc(DOCTYPE, self.company).as_dict(), default=str)
		self.assertNotIn("PRIVATE KEY", as_client_sees_it)

	def test_generate_csr_returns_only_the_csr(self):
		self.make(homologation_private_key=self.key, homologation_certificate=self.certificate)

		result = generate_csr(self.company, "Homologation", "erpnext-homologation")

		self.assertEqual(set(result), {"filename", "csr"})
		self.assertNotIn("PRIVATE KEY", json.dumps(result))

		doc = frappe.get_doc(DOCTYPE, self.company)
		self.assertFalse(doc.homologation_certificate)
		new_key = doc.get_private_key("Homologation")
		self.assertNotEqual(new_key, self.key)

		csr = x509.load_pem_x509_csr(result["csr"].encode())
		fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
		stored_public = serialization.load_pem_private_key(new_key.encode(), None).public_key()
		self.assertEqual(csr.public_key().public_bytes(*fmt), stored_public.public_bytes(*fmt))

	def test_generate_csr_requires_write_permission(self):
		self.make()
		with self.set_user("Guest"), self.assertRaises(frappe.PermissionError):
			generate_csr(self.company, "Homologation", "erpnext-homologation")

	def test_generate_csr_rejects_bad_name(self):
		self.make()
		with self.assertRaises(frappe.ValidationError):
			generate_csr(self.company, "Homologation", "name with spaces")
