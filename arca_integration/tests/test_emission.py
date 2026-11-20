from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import frappe

from arca_integration.arca import emission
from arca_integration.services.errors import TransportError, TransportTimeout
from arca_integration.services.mtxca.client import (
	Authorization,
	AuthorizedVoucher,
	CodeDescription,
	MtxcaError,
)
from arca_integration.tests.test_invoice_review import TestInvoiceReview

CAE = "76123456789012"


def authorization(number=1, result="A", observations=()):
	return Authorization(result, CAE, date(2030, 1, 10), number, date.today(), tuple(observations))


class TestEmission(TestInvoiceReview):
	"""Submitting an invoice requests the CAE (ARCA is replaced by a fake client)."""

	def setUp(self):
		super().setUp()
		self.client = MagicMock()
		self.client.exchanges = []
		self.client.last_authorized.return_value = 0
		self.client.authorize.side_effect = lambda voucher: authorization(voucher.number)
		for target, value in {
			"get_mtxca_client": MagicMock(return_value=self.client),
			"get_access_ticket": MagicMock(),
		}.items():
			patcher = patch.object(emission, target, value)
			patcher.start()
			self.addCleanup(patcher.stop)

	def logs(self, doc):
		return frappe.get_all("ARCA Request Log", filters={"sales_invoice": doc.name}, pluck="result")

	def test_submit_requests_the_cae(self):
		doc = self.make_invoice()
		self.client.last_authorized.return_value = self.last_known()
		doc.submit()

		doc.reload()
		voucher = self.client.authorize.call_args.args[0]
		self.assertEqual(voucher.number, self.last_known())
		self.assertEqual(
			(doc.arca_status, doc.arca_cae, doc.arca_voucher_type, doc.arca_voucher_number),
			("Authorized", CAE, "1", voucher.number),
		)
		self.assertEqual(str(doc.arca_cae_due_date), "2030-01-10")
		self.assertEqual(doc.arca_concept, "Services")
		self.assertEqual(self.logs(doc), ["Authorized"])

	def test_observations_are_shown(self):
		doc = self.make_invoice()
		self.client.last_authorized.return_value = self.last_known()
		self.client.authorize.side_effect = lambda voucher: authorization(
			voucher.number, "O", [CodeDescription("504", "GTIN no registrado")]
		)
		frappe.message_log = []
		doc.submit()
		self.assertEqual(self.logs(doc), ["Observed"])
		self.assertTrue(any("GTIN" in str(m) for m in frappe.message_log))

	def test_rejection_keeps_the_invoice_in_draft(self):
		doc = self.make_invoice()
		self.client.last_authorized.return_value = self.last_known()
		self.client.authorize.side_effect = MtxcaError([CodeDescription("10010", "No empadronado")])
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("Codificación de Productos", str(ctx.exception))

	def test_lost_answer_is_recovered(self):
		doc = self.make_invoice()
		number = self.last_known() + 1
		self.client.last_authorized.return_value = number
		self.client.get_voucher.return_value = AuthorizedVoucher(
			1, 1, number, date.today(), CAE, date(2030, 1, 10), Decimal("121.00"), "80", "30500001735"
		)
		doc.submit()

		doc.reload()
		self.client.authorize.assert_not_called()
		self.assertEqual((doc.arca_cae, doc.arca_voucher_number), (CAE, number))

	def test_foreign_voucher_stops_the_submit(self):
		doc = self.make_invoice()
		self.client.last_authorized.return_value = 6
		self.client.get_voucher.return_value = AuthorizedVoucher(
			1, 1, 6, date.today(), CAE, None, Decimal("999.00"), "80", "30500001735"
		)
		with (
			patch.object(emission, "_last_known", return_value=5),
			self.assertRaises(frappe.ValidationError) as ctx,
		):
			doc.submit()
		self.assertIn("Another system", str(ctx.exception))
		self.client.authorize.assert_not_called()

	def test_first_use_continues_after_earlier_vouchers(self):
		"""Vouchers issued before this site used the point of sale (e.g. by another app)."""
		doc = self.make_invoice()
		self.client.last_authorized.return_value = 8
		self.client.get_voucher.return_value = None  # homologation may not find its own last voucher
		with patch.object(emission, "_last_known", return_value=0):
			doc.submit()
		self.assertEqual(self.client.authorize.call_args.args[0].number, 9)

	def test_technical_error_is_not_called_an_outage(self):
		from arca_integration.services.errors import ServiceFault

		doc = self.make_invoice()
		self.client.last_authorized.side_effect = ServiceFault("soap:Client", "Elemento inválido")
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("technical error", str(ctx.exception))
		self.assertIn("Elemento inválido", str(ctx.exception))

	def test_timeout_asks_arca_before_failing(self):
		doc = self.make_invoice()
		self.client.last_authorized.return_value = self.last_known()
		self.client.authorize.side_effect = TransportTimeout("timeout")
		number = self.last_known() + 1
		self.client.get_voucher.return_value = AuthorizedVoucher(
			1, 1, number, date.today(), CAE, date(2030, 1, 10), Decimal("121.00"), "80", "30500001735"
		)
		doc.submit()
		self.assertEqual(frappe.db.get_value("Sales Invoice", doc.name, "arca_cae"), CAE)

		other = self.make_invoice()
		self.client.get_voucher.return_value = None
		with self.assertRaises(frappe.ValidationError) as ctx:
			other.submit()
		self.assertIn("did not answer in time", str(ctx.exception))

	def test_arca_down(self):
		doc = self.make_invoice()
		self.client.last_authorized.side_effect = TransportError("down")
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("not responding", str(ctx.exception))

	def test_authorized_invoice_cannot_be_cancelled(self):
		doc = self.make_invoice()
		self.client.last_authorized.return_value = self.last_known()
		doc.submit()
		with self.assertRaises(frappe.ValidationError):
			doc.cancel()

	def last_known(self):
		numbers = frappe.get_all(
			"Sales Invoice",
			filters={"company": self.company, "arca_voucher_type": "1", "docstatus": ["in", [1, 2]]},
			pluck="arca_voucher_number",
			order_by="arca_voucher_number desc",
			limit=1,
		)
		return int(numbers[0] or 0) if numbers else 0
