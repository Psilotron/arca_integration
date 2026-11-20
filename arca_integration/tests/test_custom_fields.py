import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca.custom_fields import (
	MODULE,
	PREFIX,
	create_arca_custom_fields,
	delete_arca_custom_fields,
	get_custom_fields,
)
from arca_integration.arca.events import customer

EMISSION_FIELDS = [
	"arca_status",
	"arca_voucher_type",
	"arca_point_of_sale",
	"arca_voucher_number",
	"arca_cae",
	"arca_cae_due_date",
]


def installed() -> set[tuple[str, str]]:
	return {
		(row.dt, row.fieldname)
		for row in frappe.get_all("Custom Field", filters={"module": MODULE}, fields=["dt", "fieldname"])
	}


class TestCustomFields(IntegrationTestCase):
	def test_every_field_is_installed_with_prefix_and_module(self):
		expected = {(dt, f["fieldname"]) for dt, fields in get_custom_fields().items() for f in fields}
		self.assertTrue(all(name.startswith(PREFIX) for _, name in expected))
		self.assertEqual(installed() & expected, expected)

	def test_creation_is_idempotent(self):
		before = installed()
		create_arca_custom_fields()
		create_arca_custom_fields()
		self.assertEqual(installed(), before)

	def test_emission_fields_are_read_only_and_never_copied(self):
		meta = frappe.get_meta("Sales Invoice")
		for fieldname in EMISSION_FIELDS:
			field = meta.get_field(fieldname)
			self.assertTrue(field.read_only and field.no_copy and field.allow_on_submit, fieldname)

		invoice = frappe.new_doc("Sales Invoice")
		invoice.update({"arca_cae": "12345678901234", "arca_voucher_number": 7, "arca_status": "Authorized"})
		# what Duplicate and Amend do in the UI
		copy = frappe.copy_doc(invoice, ignore_no_copy=False)
		self.assertEqual((copy.arca_cae, copy.arca_voucher_number, copy.arca_status), (None, None, None))

	def test_uninstall_removes_every_field(self):
		try:
			delete_arca_custom_fields()
			self.assertEqual(installed(), set())
			self.assertIsNone(frappe.get_meta("Sales Invoice").get_field("arca_cae"))
		finally:
			create_arca_custom_fields()


class TestCustomerValidation(IntegrationTestCase):
	def customer(self, tax_id, document_type=None):
		doc = frappe.new_doc("Customer")
		doc.update({"customer_name": "Test", "tax_id": tax_id, "arca_document_type": document_type})
		return doc

	def test_cuit_is_detected_and_normalized(self):
		doc = self.customer("30-00000000-7")
		customer.validate(doc)
		self.assertEqual((doc.tax_id, doc.arca_document_type), ("30000000007", "80"))

	def test_invalid_cuit_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			customer.validate(self.customer("30000000008", "80"))

	def test_other_documents_are_not_checked(self):
		doc = self.customer("12345678", "96")
		customer.validate(doc)
		self.assertEqual(doc.tax_id, "12345678")

	def test_no_tax_id(self):
		doc = self.customer(None)
		customer.validate(doc)
		self.assertIsNone(doc.arca_document_type)
