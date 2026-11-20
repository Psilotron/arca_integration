import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca import connection
from arca_integration.services.credentials import generate_private_key
from arca_integration.services.errors import AuthError, TransportTimeout
from arca_integration.services.mtxca.client import PointOfSale
from arca_integration.services.wsaa import Ticket
from arca_integration.tests.test_credentials import make_certificate
from arca_integration.tests.utils import get_test_company

UP = SimpleNamespace(app_server="OK", db_server="OK", auth_server="OK")


class TestConnection(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()

	def setUp(self):
		frappe.delete_doc_if_exists("ARCA Company Settings", self.company, force=True)
		frappe.db.delete("ARCA Point of Sale", {"company": self.company})
		key = generate_private_key()
		frappe.get_doc(
			{
				"doctype": "ARCA Company Settings",
				"company": self.company,
				"homologation_private_key": key,
				"homologation_certificate": make_certificate(key),
			}
		).insert()
		frappe.db.set_value("Onboarding Step", {"name": ["like", "ARCA%"]}, "is_complete", 0)

		self.points = [PointOfSale(3, False, None)]
		self.status_client = MagicMock()
		self.status_client.dummy.return_value = UP
		self.service_client = MagicMock()
		self.service_client.points_of_sale_cae.side_effect = lambda: self.points
		self.login = MagicMock(return_value=Ticket("T", "S", None))

		for target, value in {
			"get_status_client": MagicMock(return_value=self.status_client),
			"obtain_ticket": self.login,
			"mtxca_client_for": MagicMock(return_value=self.service_client),
			"sync_catalogs": MagicMock(),
		}.items():
			patcher = patch.object(connection, target, value)
			patcher.start()
			self.addCleanup(patcher.stop)

	def steps(self, result):
		return {step["key"]: step["status"] for step in result["steps"]}

	def test_everything_ready_picks_the_only_point_of_sale(self):
		result = connection.verify_connection(self.company)

		self.assertEqual(result["status"], "Ready")
		self.assertEqual(set(self.steps(result).values()), {"ok"})
		settings = frappe.get_doc("ARCA Company Settings", self.company)
		self.assertTrue(settings.default_point_of_sale.endswith("-00003"))
		self.assertEqual(settings.connection_status, "Ready")
		self.assertEqual(len(json.loads(settings.connection_report)), 7)
		self.assertEqual(frappe.db.get_value("Onboarding Step", "ARCA Connection", "is_complete"), 1)

	def test_missing_certificate_stops_before_calling_arca(self):
		settings = frappe.get_doc("ARCA Company Settings", self.company)
		settings.homologation_certificate = None
		settings.homologation_private_key = None
		settings.save()

		result = connection.verify_connection(self.company)

		steps = self.steps(result)
		self.assertEqual(steps["settings"], "error")
		self.assertEqual(steps["login"], "skipped")
		self.login.assert_not_called()
		self.assertEqual(result["status"], "Needs Attention")

	def test_unauthorized_certificate_is_explained(self):
		self.login.side_effect = AuthError("Computador no autorizado", detail="ns1:coe.notAuthorized")

		result = connection.verify_connection(self.company)

		login = next(step for step in result["steps"] if step["key"] == "login")
		self.assertEqual(login["status"], "error")
		self.assertIn("WSASS", login["action"])
		self.assertIn("coe.notAuthorized", login["detail"])
		self.assertEqual(self.steps(result)["points_of_sale"], "skipped")

	def test_arca_down(self):
		self.status_client.dummy.side_effect = TransportTimeout("timeout")
		result = connection.verify_connection(self.company)
		self.assertEqual(self.steps(result)["arca"], "error")
		self.login.assert_not_called()

	def test_several_points_of_sale_ask_to_choose(self):
		self.points = [PointOfSale(3, False, None), PointOfSale(4, False, None), PointOfSale(5, True, None)]
		result = connection.verify_connection(self.company)
		self.assertEqual(self.steps(result)["points_of_sale"], "warning")
		self.assertFalse(frappe.db.get_value("ARCA Company Settings", self.company, "default_point_of_sale"))

	def test_no_usable_point_of_sale(self):
		self.points = [PointOfSale(5, True, None), PointOfSale(6, False, date(2026, 1, 1))]
		result = connection.verify_connection(self.company)
		self.assertEqual(self.steps(result)["points_of_sale"], "error")

	def test_certificate_close_to_expiry_is_a_warning(self):
		with patch.object(connection, "days_until", return_value=10):
			result = connection.verify_connection(self.company)
		self.assertEqual(self.steps(result)["settings"], "warning")
		self.assertEqual(result["status"], "Needs Attention")

	def test_requires_write_permission(self):
		with self.set_user("Guest"), self.assertRaises(frappe.PermissionError):
			connection.verify_connection(self.company)

	def test_expiring_certificate_notifies_system_managers(self):
		frappe.db.delete("Notification Log", {"document_type": "ARCA Company Settings"})
		with patch.object(connection, "days_until", return_value=7):
			connection.notify_expiring_certificates()
		subjects = frappe.get_all(
			"Notification Log", filters={"document_name": self.company}, pluck="subject"
		)
		self.assertTrue(subjects)
		self.assertIn("7", subjects[0])

		frappe.db.delete("Notification Log", {"document_type": "ARCA Company Settings"})
		with patch.object(connection, "days_until", return_value=12):
			connection.notify_expiring_certificates()
		self.assertFalse(frappe.db.exists("Notification Log", {"document_name": self.company}))
