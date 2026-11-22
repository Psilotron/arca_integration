from decimal import Decimal
from unittest.mock import patch

import frappe

from arca_integration.arca import currency, invoice
from arca_integration.services.mtxca.validation import Check, Issue
from arca_integration.tests.utils import CUSTOMER, VAT_ACCOUNT, ArcaInvoiceTestCase, ensure


class TestInvoiceReview(ArcaInvoiceTestCase):
	def test_ready_invoice_a_for_services(self):
		doc = self.make_invoice()
		review = invoice.get_review(doc.name)

		self.assertTrue(review["applies"])
		self.assertTrue(review["ready"], review["issues"])
		voucher = review["voucher"]
		self.assertEqual((voucher["voucher_type"], voucher["point_of_sale"]), (1, 1))
		self.assertEqual((float(voucher["taxed"]), float(voucher["total"])), (100.0, 121.0))
		self.assertIsNotNone(voucher["service_start"])

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
				explained = invoice.explain(Issue(check, row=1, values=(1, 2, 3, 4)), doc)
				self.assertTrue(explained["message"])


OFFICIAL = Decimal("1181.524")


DOLLAR_DEBTORS = "Debtors USD - _TARCA"


class TestForeignCurrencyReview(ArcaInvoiceTestCase):
	def dollars(self, conversion_rate=1200, **values):
		# ERPNext needs a receivable account in the invoice currency.
		ensure(
			"Account",
			DOLLAR_DEBTORS,
			{
				"account_name": "Debtors USD",
				"parent_account": "Accounts Receivable - _TARCA",
				"company": self.company,
				"account_type": "Receivable",
				"account_currency": "USD",
			},
		)
		return self.make_invoice(
			currency="USD", conversion_rate=conversion_rate, debit_to=DOLLAR_DEBTORS, **values
		)

	def test_paid_in_dollars_offers_arca_rate(self):
		doc = self.dollars(arca_paid_in_currency=1)
		with patch("arca_integration.arca.invoice.official_rate", return_value=OFFICIAL):
			review = invoice.get_review(doc.name)

		self.assertEqual([issue["check"] for issue in review["issues"]], ["exchange_rate_not_official"])
		self.assertEqual(review["issues"][0]["action"]["set"], {"conversion_rate": 1181.524})

	def test_ready_in_dollars(self):
		doc = self.dollars(conversion_rate=float(OFFICIAL), arca_paid_in_currency=1)
		with patch("arca_integration.arca.invoice.official_rate", return_value=OFFICIAL):
			review = invoice.get_review(doc.name)

		self.assertTrue(review["ready"], review["issues"])
		voucher = review["voucher"]
		self.assertEqual((voucher["currency"], voucher["paid_in_currency"]), ("USD", True))
		self.assertEqual((float(voucher["exchange_rate"]), float(voucher["total"])), (1181.524, 121.0))


class TestOfficialRate(ArcaInvoiceTestCase):
	def setUp(self):
		frappe.cache.delete_keys("arca_rate:")

	def test_failures_are_silent(self):
		with patch(
			"arca_integration.arca.currency.obtain_ticket",
			side_effect=frappe.ValidationError("no certificate"),
		):
			frappe.local.message_log = []
			self.assertIsNone(currency.official_rate(self.company, "DOL", frappe.utils.nowdate()))
		self.assertEqual(frappe.local.message_log, [])

	def test_rate_is_cached(self):
		with (
			patch("arca_integration.arca.currency.obtain_ticket"),
			patch("arca_integration.arca.currency.mtxca_client_for") as client_for,
		):
			client_for.return_value.exchange_rate.return_value = OFFICIAL
			for _ in range(2):
				self.assertEqual(currency.official_rate(self.company, "DOL", "2026-11-20"), OFFICIAL)
		client_for.return_value.exchange_rate.assert_called_once()

	def test_no_rate_for_pesos(self):
		self.assertIsNone(currency.official_rate(self.company, "PES", "2026-11-20"))
