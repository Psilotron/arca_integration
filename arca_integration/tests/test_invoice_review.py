import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from arca_integration.arca import invoice
from arca_integration.arca.catalogs import create_test_point_of_sale, link_standard_records
from arca_integration.services.mtxca.validation import Check, Issue
from arca_integration.tests.utils import get_test_company

CUSTOMER = "_Test ARCA Customer"
ITEM = "_Test ARCA Service"
VAT_ACCOUNT = "IVA Débito Fiscal - _TARCA"


def ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


class TestInvoiceReview(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()
		ensure("ARCA Company Settings", cls.company, {"company": cls.company, "cuit": "30000000007"})
		point = create_test_point_of_sale(cls.company)
		frappe.db.set_value("ARCA Company Settings", cls.company, "default_point_of_sale", point.name)

		for code, description, voucher_types in (
			("1", "IVA Responsable Inscripto", (1, 2, 3)),
			("5", "Consumidor Final", (6, 7, 8)),
		):
			ensure(
				"ARCA Receiver VAT Condition",
				code,
				{
					"code": code,
					"description": description,
					"voucher_types": [{"voucher_type": str(t)} for t in voucher_types],
				},
			)
		for doctype, code, description in (
			("ARCA Unit of Measure", "7", "unidades"),
			("ARCA Currency", "PES", "Pesos Argentinos"),
			("ARCA Document Type", "80", "CUIT"),
		):
			ensure(doctype, code, {"code": code, "description": description})
		link_standard_records()

		ensure(
			"Account",
			VAT_ACCOUNT,
			{
				"account_name": "IVA Débito Fiscal",
				"parent_account": "Duties and Taxes - _TARCA",
				"company": cls.company,
				"account_type": "Tax",
				"arca_is_vat": 1,
			},
		)
		ensure(
			"Customer",
			CUSTOMER,
			{
				"customer_name": CUSTOMER,
				"customer_type": "Company",
				"tax_id": "30-50000173-5",
				"arca_document_type": "80",
				"arca_receiver_vat_condition": "1",
			},
		)
		ensure(
			"Item",
			ITEM,
			{"item_code": ITEM, "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0},
		)

	def make_invoice(self, rate=100, vat_rate=21):
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": CUSTOMER,
				"currency": "ARS",
				"posting_date": nowdate(),
				"due_date": nowdate(),
				"items": [{"item_code": ITEM, "qty": 1, "rate": rate}],
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": VAT_ACCOUNT,
						"description": "IVA 21%",
						"rate": vat_rate,
					}
				],
			}
		)
		return doc.insert(ignore_permissions=True)

	def test_ready_invoice_a_for_services(self):
		doc = self.make_invoice()
		review = invoice.get_review(doc.name)

		self.assertTrue(review["applies"])
		self.assertTrue(review["ready"], review["issues"])
		voucher = review["voucher"]
		self.assertEqual((voucher["voucher_type"], voucher["point_of_sale"]), (1, 1))
		self.assertEqual((float(voucher["taxed"]), float(voucher["total"])), (100.0, 121.0))
		self.assertIsNotNone(voucher["service_start"])

		doc.submit()
		self.assertEqual(doc.docstatus, 1)

	def test_rejected_invoice_cannot_be_submitted(self):
		frappe.db.set_value("Customer", CUSTOMER, "arca_receiver_vat_condition", None)
		self.addCleanup(frappe.db.set_value, "Customer", CUSTOMER, "arca_receiver_vat_condition", "1")
		doc = self.make_invoice()

		review = invoice.get_review(doc.name)
		self.assertEqual([issue["check"] for issue in review["issues"]], ["receiver_vat_condition_missing"])
		self.assertEqual(review["issues"][0]["action"]["doctype"], "Customer")
		with self.assertRaises(frappe.ValidationError):
			doc.submit()

	def test_vat_condition_from_the_invoice_rate(self):
		doc = self.make_invoice(vat_rate=10.5)
		review = invoice.get_review(doc.name)
		self.assertTrue(review["ready"], review["issues"])
		self.assertEqual(review["voucher"]["vat_subtotals"][0]["amount"], 10.5)

	def test_unmarked_tax_account_is_explained(self):
		doc = self.make_invoice()
		frappe.db.set_value("Account", VAT_ACCOUNT, "arca_is_vat", 0)
		self.addCleanup(frappe.db.set_value, "Account", VAT_ACCOUNT, "arca_is_vat", 1)

		checks = {issue["check"] for issue in invoice.get_review(doc.name)["issues"]}
		self.assertIn("tax_account_unmapped", checks)

	def test_companies_without_arca_are_left_alone(self):
		doc = frappe._dict(doctype="Sales Invoice", company="Not ARCA", is_return=0)
		self.assertFalse(invoice.applies(doc))

	def test_every_check_has_a_message(self):
		doc = self.make_invoice()
		for check in Check:
			with self.subTest(check):
				explained = invoice.explain(Issue(check, row=1, values=(1, 2)), doc)
				self.assertTrue(explained["message"])
