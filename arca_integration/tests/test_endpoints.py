from frappe.tests import UnitTestCase

from arca_integration.services.endpoints import (
	DEFAULT_ENDPOINTS,
	Environment,
	Service,
	UnknownEndpoint,
	resolve_endpoint,
)


class TestEndpoints(UnitTestCase):
	def test_every_service_has_every_environment(self):
		for service in Service:
			for environment in Environment:
				self.assertIn((service, environment), DEFAULT_ENDPOINTS)

	def test_all_defaults_use_https(self):
		for url in DEFAULT_ENDPOINTS.values():
			self.assertTrue(url.startswith("https://"), url)

	def test_resolves_default(self):
		self.assertEqual(
			resolve_endpoint("wsmtxca", "Homologation"),
			"https://fwshomo.afip.gov.ar/wsmtxca/services/MTXCAService",
		)

	def test_override_wins(self):
		overrides = {("wsaa", "Production"): "https://example.test/LoginCms"}
		self.assertEqual(
			resolve_endpoint(Service.WSAA, Environment.PRODUCTION, overrides), "https://example.test/LoginCms"
		)
		self.assertEqual(
			resolve_endpoint(Service.WSAA, Environment.HOMOLOGATION, overrides),
			DEFAULT_ENDPOINTS[(Service.WSAA, Environment.HOMOLOGATION)],
		)

	def test_unknown_service(self):
		with self.assertRaises(UnknownEndpoint):
			resolve_endpoint("wsfe", "Homologation")
