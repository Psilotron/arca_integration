import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from arca_integration.arca.catalogs import create_test_point_of_sale, link_standard_records

TEST_COMPANY = "_Test ARCA Company"


def get_test_company() -> str:
	"""Minimal Argentine company for integration tests, created once per site."""
	if not frappe.db.exists("Company", TEST_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": TEST_COMPANY,
				"abbr": "_TARCA",
				"default_currency": "ARS",
				"country": "Argentina",
				"tax_id": "30-00000000-7",
			}
		).insert(ignore_permissions=True)
	return TEST_COMPANY


CUSTOMER = "_Test ARCA Customer"
ITEM = "_Test ARCA Service"
VAT_ACCOUNT = "IVA Débito Fiscal - _TARCA"


def ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


class ArcaInvoiceTestCase(IntegrationTestCase):
	"""Base for tests that write invoices of the test company. It has no tests of its own.

	Test classes must not subclass each other: Frappe marks a class as set up
	with a class attribute, and a subclass inherits it, so its setUpClass is
	skipped and nothing it writes is rolled back.
	"""

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
			("ARCA Currency", "DOL", "Dólar Estadounidense"),
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

	def tearDown(self):
		# Logs of failed submits wait for a rollback; the class rollback would write them for good.
		frappe.db.after_rollback.reset()
		super().tearDown()

	def make_invoice(self, rate=100, vat_rate=21, currency="ARS", conversion_rate=1, **values):
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": CUSTOMER,
				"currency": currency,
				"conversion_rate": conversion_rate,
				**values,
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
