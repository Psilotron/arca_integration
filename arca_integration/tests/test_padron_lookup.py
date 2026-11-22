from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca import padron
from arca_integration.services.errors import AuthError
from arca_integration.services.padron import TaxpayerNotFound
from arca_integration.tests.test_padron import parse
from arca_integration.tests.utils import get_test_company


class TestPadronLookup(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()
		if not frappe.db.exists("ARCA Company Settings", cls.company):
			frappe.get_doc({"doctype": "ARCA Company Settings", "company": cls.company}).insert()
		for code, description in (("1", "IVA Responsable Inscripto"), ("5", "Consumidor Final")):
			if not frappe.db.exists("ARCA Receiver VAT Condition", code):
				frappe.get_doc(
					{"doctype": "ARCA Receiver VAT Condition", "code": code, "description": description}
				).insert(ignore_permissions=True)

	def setUp(self):
		self.client = MagicMock()
		patcher = patch.object(padron, "get_padron_client", return_value=self.client)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_company_proposal(self):
		self.client.get_taxpayer.return_value = parse("persona_juridica")
		proposal = padron.lookup("30-50000173-5", self.company)

		self.assertEqual(proposal["name"], "BANCO PRUEBA 02")
		self.assertEqual(proposal["customer_type"], "Company")
		self.assertEqual(
			(proposal["vat_condition"], proposal["vat_condition_label"]), ("1", "IVA Responsable Inscripto")
		)
		self.assertEqual(proposal["address"]["address_line1"], "AV. CORRIENTES 1234")
		self.assertEqual(proposal["warnings"], [])

	def test_blocked_constancia_warns_and_proposes_no_condition(self):
		self.client.get_taxpayer.return_value = parse("persona_error")
		proposal = padron.lookup("20222222223", self.company)
		self.assertIsNone(proposal["vat_condition"])
		self.assertEqual(len(proposal["warnings"]), 1)

	def test_invalid_cuit_is_rejected_before_calling_arca(self):
		with self.assertRaises(frappe.ValidationError):
			padron.lookup("20222222224", self.company)
		self.client.get_taxpayer.assert_not_called()

	def test_errors_are_explained(self):
		for error in (
			TaxpayerNotFound("No existe persona con ese Id"),
			AuthError("x", detail="ns1:coe.notAuthorized"),
		):
			self.client.get_taxpayer.side_effect = error
			with self.subTest(error=type(error).__name__), self.assertRaises(frappe.ValidationError):
				padron.lookup("20000000001", self.company)
		self.assertIn("ws_sr_constancia_inscripcion", frappe.local.message_log[-1]["message"])

	def test_resolve_company(self):
		self.assertEqual(padron.resolve_company(self.company), self.company)
		with self.assertRaises(frappe.ValidationError):
			padron.resolve_company("Company Without Settings")

	def test_apply_address_creates_then_updates(self):
		customer = frappe.get_doc({"doctype": "Customer", "customer_name": "_Test ARCA Padron Customer"})
		customer.insert(ignore_permissions=True, ignore_if_duplicate=True)
		address = {
			"address_line1": "AV. CORRIENTES 1234",
			"city": "CAPITAL FEDERAL",
			"state": "CABA",
			"pincode": "1043",
		}

		first = padron.apply_address("Customer", customer.name, address)
		second = padron.apply_address("Customer", customer.name, {**address, "pincode": "1044"})

		self.assertEqual(first, second)
		doc = frappe.get_doc("Address", first)
		self.assertEqual((doc.pincode, doc.country, doc.address_type), ("1044", "Argentina", "Billing"))
		self.assertEqual(doc.links[0].link_name, customer.name)

	def test_apply_address_only_for_customers_and_companies(self):
		with self.assertRaises(frappe.ValidationError):
			padron.apply_address("Supplier", "x", {})
