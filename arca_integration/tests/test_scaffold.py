import frappe
from frappe.tests import IntegrationTestCase


class TestScaffold(IntegrationTestCase):
	def test_requires_erpnext(self):
		self.assertIn("erpnext", frappe.get_hooks("required_apps", app_name="arca_integration"))

	def test_arca_module_is_registered(self):
		self.assertEqual(frappe.db.get_value("Module Def", "ARCA", "app_name"), "arca_integration")
