import base64
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import add_days, nowdate

from arca_integration.arca import purchase
from arca_integration.services.mtxca.client import CodeDescription
from arca_integration.services.wscdc.client import Verification
from arca_integration.tests.utils import ITEM, ArcaInvoiceTestCase, ensure

SUPPLIER = "_Test ARCA Supplier"
SUPPLIER_CUIT = "20111111112"
CAE = "71234567890123"


def qr_link(**overrides):
	data = {"ver": 1, "fecha": nowdate(), "cuit": int(SUPPLIER_CUIT), "ptoVta": 3, "tipoCmp": 1, "nroCmp": 15,
		"importe": 121, "moneda": "PES", "ctz": 1, "tipoDocRec": 80, "nroDocRec": 30000000007,
		"tipoCodAut": "E", "codAut": int(CAE), **overrides}  # fmt: skip
	return "https://www.arca.gob.ar/fe/qr/?p=" + base64.b64encode(json.dumps(data).encode()).decode()


class TestPurchaseVerification(ArcaInvoiceTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure(
			"Supplier",
			SUPPLIER,
			{"supplier_name": SUPPLIER, "supplier_type": "Company", "tax_id": "20-11111111-2"},
		)
		frappe.db.set_value("Item", ITEM, "is_purchase_item", 1)

	def setUp(self):
		super().setUp()
		self.client = MagicMock()
		self.client.exchanges = []
		self.client.verify.return_value = Verification(approved=True, processed_at="20261121120000")
		patcher = patch.object(purchase, "get_wscdc_client", return_value=self.client)
		patcher.start()
		self.addCleanup(patcher.stop)

	def make_purchase(self, with_voucher=True, **values):
		doc = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": self.company,
				"supplier": SUPPLIER,
				"posting_date": nowdate(),
				"bill_no": "00003-00000015",
				"bill_date": nowdate(),
				"items": [{"item_code": ITEM, "qty": 1, "rate": 121}],
				**values,
			}
		)
		if with_voucher:
			doc.update(purchase.read_qr(qr_link(), self.company, SUPPLIER)["values"])
		return doc.insert(ignore_permissions=True)

	def test_qr_fills_the_voucher(self):
		read = purchase.read_qr(qr_link(), self.company)
		self.assertEqual(read["warnings"], [])
		self.assertEqual(
			read["values"],
			{
				"arca_authorization_mode": "CAE",
				"arca_authorization_code": CAE,
				"arca_voucher_type": "1",
				"arca_point_of_sale": 3,
				"arca_voucher_number": 15,
				"bill_no": "00003-00000015",
				"bill_date": nowdate(),
				"supplier": SUPPLIER,  # found by its CUIT
			},
		)

	def test_qr_of_another_receiver_or_issuer_is_flagged(self):
		read = purchase.read_qr(qr_link(nroDocRec=30500001735, cuit=20222222223), self.company, SUPPLIER)
		self.assertEqual(len(read["warnings"]), 2, read["warnings"])

	def test_not_a_qr(self):
		with self.assertRaises(frappe.ValidationError):
			purchase.read_qr("https://example.com", self.company)

	def test_submit_needs_the_voucher_data(self):
		doc = self.make_purchase(with_voucher=False)
		with self.assertRaises(frappe.ValidationError):
			doc.submit()
		self.client.verify.assert_not_called()

	def test_submit_verifies_with_arca(self):
		doc = self.make_purchase()
		doc.submit()

		voucher = self.client.verify.call_args.args[0]
		self.assertEqual(
			(voucher.issuer_cuit, voucher.point_of_sale, voucher.number, voucher.authorization_code),
			(SUPPLIER_CUIT, 3, 15, CAE),
		)
		self.assertEqual((voucher.receiver_document_number, float(voucher.total)), ("30000000007", 121.0))
		doc.reload()
		self.assertEqual(doc.arca_verification, "Verified")
		self.assertTrue(purchase.is_verified(doc))
		self.assertIn(
			"Verified", frappe.get_all("ARCA Request Log", {"purchase_invoice": doc.name}, pluck="result")
		)

	def test_rejected_voucher_cannot_be_submitted(self):
		self.client.verify.return_value = Verification(
			approved=False, observations=(CodeDescription("110", "Importe no coincide"),)
		)
		doc = self.make_purchase()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		explanation = purchase.EXPLANATIONS["110"]
		self.assertIn(frappe._(explanation), str(ctx.exception))

	def test_rejection_is_kept_until_the_data_changes(self):
		self.client.verify.return_value = Verification(
			approved=False, observations=(CodeDescription("100", "No existe"),)
		)
		doc = self.make_purchase()
		state = purchase.verify_in_arca(doc.name)
		self.assertEqual((state["verified"], state["verification"], state["outdated"]), (False, "Rejected", False))

	def test_verify_button_then_a_change_voids_it(self):
		doc = self.make_purchase()
		self.assertTrue(purchase.verify_in_arca(doc.name)["verified"])

		doc.reload()
		doc.bill_date = add_days(nowdate(), -1)
		doc.save()
		self.assertEqual(doc.arca_verification, "")
		self.assertFalse(purchase.status(doc)["verified"])

	def test_exempt_supplier_is_not_verified(self):
		frappe.db.set_value("Supplier", SUPPLIER, "arca_no_vouchers", 1)
		self.addCleanup(frappe.db.set_value, "Supplier", SUPPLIER, "arca_no_vouchers", 0)
		doc = self.make_purchase(with_voucher=False)
		doc.submit()
		self.client.verify.assert_not_called()
		self.assertFalse(purchase.status(doc)["applies"])

	def test_only_accounts_managers_exempt_suppliers(self):
		supplier = frappe.get_doc("Supplier", SUPPLIER)
		supplier.arca_no_vouchers = 1
		with patch.object(purchase.frappe, "get_roles", return_value=["Purchase User"]):
			with self.assertRaises(frappe.PermissionError):
				supplier.save()
		supplier.reload()
		supplier.arca_no_vouchers = 1
		with patch.object(purchase.frappe, "get_roles", return_value=["Accounts Manager"]):
			supplier.save()
		self.addCleanup(frappe.db.set_value, "Supplier", SUPPLIER, "arca_no_vouchers", 0)
