"""Calls to ARCA homologation. Skipped unless ARCA_LIVE=1.

ARCA_LIVE_CERT and ARCA_LIVE_KEY point to a homologation certificate and its
private key, enabled for wsmtxca. The ticket is stored like in production, so running this test
again within 12 hours reuses it instead of failing with alreadyAuthenticated.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest import skipUnless

import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import get_access_ticket
from arca_integration.arca.mtxca import get_mtxca_client
from arca_integration.services.credentials import read_certificate
from arca_integration.tests.utils import get_test_company


@skipUnless(os.environ.get("ARCA_LIVE") == "1", "set ARCA_LIVE=1 to call ARCA homologation")
class TestLiveArca(IntegrationTestCase):
	def setUp(self):
		certificate = Path(os.environ["ARCA_LIVE_CERT"]).read_text()
		private_key = Path(os.environ["ARCA_LIVE_KEY"]).read_text()
		company = get_test_company()

		settings = (
			frappe.get_doc("ARCA Company Settings", company)
			if frappe.db.exists("ARCA Company Settings", company)
			else frappe.new_doc("ARCA Company Settings")
		)
		settings.update(
			{
				"company": company,
				"cuit": read_certificate(certificate).cuit,
				"environment": "Homologation",
				"homologation_certificate": certificate,
				"homologation_private_key": private_key,
			}
		)
		settings.save()
		frappe.db.commit()  # nosemgrep: keep the settings the stored ticket belongs to
		self.company = company

	def test_login_for_wsmtxca(self):
		ticket = get_access_ticket(self.company, "wsmtxca")

		self.assertTrue(ticket.token and ticket.sign)
		self.assertGreater(ticket.expires_at, datetime.now(UTC))

	def test_mtxca_voucher_types(self):
		codes = {entry.code for entry in get_mtxca_client(self.company).voucher_types()}
		self.assertIn("1", codes)
		self.assertIn("6", codes)
