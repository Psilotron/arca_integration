from datetime import date
from unittest.mock import MagicMock, patch

import frappe

from arca_integration.arca import emission
from arca_integration.arca.printing import arca_print_data
from arca_integration.services.mtxca.client import Authorization
from arca_integration.tests.utils import CUSTOMER, ArcaInvoiceTestCase

CAE = "76123456789012"
CONSUMER = "_Test ARCA Consumer"


class TestPrinting(ArcaInvoiceTestCase):
	"""The "Factura ARCA" print format (ARCA is replaced by a fake client)."""

	def setUp(self):
		super().setUp()
		self.client = MagicMock()
		self.client.exchanges = []
		self.client.last_authorized.return_value = 0
		self.client.get_voucher.return_value = None
		self.client.authorize.side_effect = lambda v: Authorization(
			"A", CAE, date(2030, 1, 10), v.number, v.issue_date, ()
		)
		for target, value in {
			"get_mtxca_client": MagicMock(return_value=self.client),
			"get_access_ticket": MagicMock(),
			"_last_known": MagicMock(return_value=0),
		}.items():
			patcher = patch.object(emission, target, value)
			patcher.start()
			self.addCleanup(patcher.stop)

	def authorized(self, **kwargs):
		doc = self.make_invoice(**kwargs)
		doc.submit()
		doc.reload()
		return doc

	def html(self, doc):
		return frappe.get_print("Sales Invoice", doc.name, print_format="Factura ARCA", doc=doc)

	def test_authorized_invoice(self):
		doc = self.authorized()
		data = arca_print_data(doc)
		self.assertEqual((data["letter"], data["code"], data["title"]), ("A", "01", "FACTURA"))
		self.assertEqual((data["point_of_sale"], data["number"]), ("00001", "00000001"))
		self.assertTrue(data["qr"].startswith("data:image/svg+xml;base64,"))
		self.assertTrue(data["show_vat"])
		html = self.html(doc)
		self.assertIn(CAE, html)
		self.assertIn("data:image/svg+xml;base64,", html)
		self.assertNotIn("NO CAE", html)

	def test_without_cae(self):
		doc = self.make_invoice()
		data = arca_print_data(doc)
		self.assertFalse(data["authorized"])
		self.assertIsNone(data["qr"])
		self.assertIn("NO CAE", self.html(doc))

	def test_homologation_is_marked(self):
		doc = self.authorized()
		self.assertTrue(arca_print_data(doc)["homologation"])
		self.assertIn("HOMOLOGATION", self.html(doc))

	def test_letter_is_kept(self):
		doc = self.authorized()
		frappe.db.set_value("Customer", CUSTOMER, "arca_receiver_vat_condition", "5")
		self.addCleanup(frappe.db.set_value, "Customer", CUSTOMER, "arca_receiver_vat_condition", "1")
		self.assertEqual(arca_print_data(doc)["letter"], "A")

	def test_credit_note(self):
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

		invoice = self.authorized()
		note = make_sales_return(invoice.name).insert(ignore_permissions=True)
		note.submit()
		note.reload()
		data = arca_print_data(note)
		self.assertEqual((data["title"], data["code"]), ("NOTA DE CRÉDITO", "03"))
		self.assertEqual(data["associated"][0]["number"], f"00001-{invoice.arca_voucher_number:08d}")

	def test_final_consumer_sees_the_vat_included(self):
		if not frappe.db.exists("Customer", CONSUMER):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": CONSUMER,
					"customer_type": "Individual",
					"arca_receiver_vat_condition": "5",
				}
			).insert(ignore_permissions=True)
		doc = self.make_invoice()
		doc.customer = CONSUMER
		doc.save()
		data = arca_print_data(doc)
		self.assertEqual(data["letter"], "B")
		self.assertFalse(data["show_vat"])
		self.assertEqual(float(data["transparency"]["vat"]), 21.0)
		self.assertIn("27.743", self.html(doc))
