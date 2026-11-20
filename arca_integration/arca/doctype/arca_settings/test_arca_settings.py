# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca.doctype.arca_settings.arca_settings import get_endpoint
from arca_integration.services.endpoints import DEFAULT_ENDPOINTS, Environment, Service


class TestARCASettings(IntegrationTestCase):
	def setUp(self):
		self.settings = frappe.get_single("ARCA Settings")
		self.settings.endpoint_overrides = []
		self.settings.save()

	def tearDown(self):
		self.settings.reload()
		self.settings.endpoint_overrides = []
		self.settings.save()

	def add_override(self, url, service="wsaa", environment="Homologation"):
		self.settings.append(
			"endpoint_overrides", {"service": service, "environment": environment, "url": url}
		)

	def test_default_endpoint(self):
		self.assertEqual(
			get_endpoint("wsmtxca", "Production"),
			DEFAULT_ENDPOINTS[(Service.WSMTXCA, Environment.PRODUCTION)],
		)

	def test_override_is_used(self):
		self.add_override("https://example.test/LoginCms")
		self.settings.save()
		self.assertEqual(get_endpoint("wsaa", "Homologation"), "https://example.test/LoginCms")
		self.assertEqual(
			get_endpoint("wsaa", "Production"), DEFAULT_ENDPOINTS[(Service.WSAA, Environment.PRODUCTION)]
		)

	def test_duplicate_override_is_rejected(self):
		self.add_override("https://a.test/")
		self.add_override("https://b.test/")
		with self.assertRaises(frappe.ValidationError):
			self.settings.save()

	def test_plain_http_is_rejected(self):
		self.add_override("http://example.test/")
		with self.assertRaises(frappe.ValidationError):
			self.settings.save()

	def test_select_options_match_registry(self):
		meta = frappe.get_meta("ARCA Endpoint Override")
		self.assertEqual(meta.get_field("service").options.split("\n"), [s.value for s in Service])
		self.assertEqual(meta.get_field("environment").options.split("\n"), [e.value for e in Environment])
		for doctype in ("ARCA Company Settings",):
			options = frappe.get_meta(doctype).get_field("environment").options.split("\n")
			self.assertEqual(options, [e.value for e in Environment])
