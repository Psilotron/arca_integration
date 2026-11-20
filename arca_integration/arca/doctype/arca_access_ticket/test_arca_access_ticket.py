# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import DOCTYPE, get_access_ticket
from arca_integration.services.credentials import generate_private_key
from arca_integration.services.errors import AlreadyAuthenticated, TransportTimeout
from arca_integration.services.wsaa import Ticket
from arca_integration.tests.test_credentials import make_certificate
from arca_integration.tests.utils import get_test_company

IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "Address", "ARCA Point of Sale", "ARCA VAT Condition"]
LOGIN = "arca_integration.services.wsaa.login"


class TestARCAAccessTicket(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()
		key = generate_private_key()
		frappe.delete_doc_if_exists("ARCA Company Settings", cls.company, force=True)
		frappe.get_doc(
			{
				"doctype": "ARCA Company Settings",
				"company": cls.company,
				"homologation_private_key": key,
				"homologation_certificate": make_certificate(key),
			}
		).insert()

	def setUp(self):
		for name in frappe.get_all(DOCTYPE, {"company": self.company}, pluck="name"):
			frappe.delete_doc(DOCTYPE, name, force=True)

	def ticket(self, hours=12, token="TOKEN"):
		return Ticket(token, "SIGN", datetime.now(UTC) + timedelta(hours=hours))

	def test_logs_in_once_and_reuses_the_stored_ticket(self):
		with patch(LOGIN, return_value=self.ticket()) as login:
			first = get_access_ticket(self.company, "wsmtxca")
			second = get_access_ticket(self.company, "wsmtxca")

		self.assertEqual(login.call_count, 1)
		self.assertEqual(login.call_args.args[1], "wsmtxca")
		self.assertTrue(login.call_args.args[2].startswith("https://wsaahomo."))
		self.assertEqual((first.token, second.token), ("TOKEN", "TOKEN"))

	def test_ticket_is_stored_encrypted(self):
		with patch(LOGIN, return_value=self.ticket(token="SECRET-TOKEN")):
			get_access_ticket(self.company, "wsmtxca")

		name = f"{self.company}-Homologation-wsmtxca"
		self.assertNotIn("SECRET-TOKEN", frappe.db.get_value(DOCTYPE, name, "token") or "")
		self.assertEqual(frappe.get_doc(DOCTYPE, name).get_password("token"), "SECRET-TOKEN")

	def test_expired_ticket_is_renewed(self):
		with patch(LOGIN, return_value=self.ticket(hours=-1, token="OLD")):
			get_access_ticket(self.company, "padron_a5")
		with patch(LOGIN, return_value=self.ticket(token="NEW")) as login:
			self.assertEqual(get_access_ticket(self.company, "padron_a5").token, "NEW")
		self.assertEqual(login.call_args.args[1], "ws_sr_padron_a5")

	def test_errors_become_validation_errors(self):
		for error in (AlreadyAuthenticated("x"), TransportTimeout("y")):
			with self.subTest(error=type(error).__name__), patch(LOGIN, side_effect=error):
				with self.assertRaises(frappe.ValidationError):
					get_access_ticket(self.company, "wsmtxca")
