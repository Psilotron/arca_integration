from datetime import date
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from arca_integration.arca import catalogs
from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.client import CodeDescription as CD
from arca_integration.services.mtxca.client import MtxcaError, PointOfSale
from arca_integration.tests.utils import get_test_company

CATALOG_DOCTYPES = [*catalogs.CODE_CATALOGS, "ARCA Receiver VAT Condition"]


class FakeClient:
	"""Stands in for MtxcaClient with in-memory answers."""

	def __init__(self):
		self.events = [CD("1", "Aviso de prueba")]
		self.answers = {
			"voucher_types": [
				CD("1", "Factura A"),
				CD("6", "Factura B"),
				CD("88", "Remito (sólo para comprobantes asociados)"),
			],
			"document_types": [CD("80", "CUIT"), CD("96", "DNI")],
			"vat_conditions": [CD("1", "No gravado"), CD("2", "Exento"), CD("5", "21%")],
			"tax_types": [CD("1", "Impuestos nacionales")],
			"units_of_measure": [CD("7", "unidades")],
			"currencies": [CD("PES", "Pesos Argentinos"), CD("DOL", "Dólar Estadounidense")],
		}
		self.receivers = {
			"1": [CD("1", "IVA Responsable Inscripto")],
			"6": [CD("5", "Consumidor Final"), CD("4", "IVA Sujeto Exento")],
		}
		self.points = [PointOfSale(3, False, None), PointOfSale(4, True, date(2026, 1, 31))]
		self.receiver_calls = []

	def __getattr__(self, name):
		if name in self.__dict__.get("answers", {}):
			return lambda: self.answers[name]
		raise AttributeError(name)

	def receiver_vat_conditions(self, voucher_type):
		self.receiver_calls.append(voucher_type)
		if voucher_type not in self.receivers:
			raise MtxcaError([CD("196", "Tipo de comprobante invalido")])
		return self.receivers[voucher_type]

	def points_of_sale_cae(self):
		return self.points


class TestCatalogSync(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()
		if not frappe.db.exists("ARCA Company Settings", cls.company):
			frappe.get_doc({"doctype": "ARCA Company Settings", "company": cls.company}).insert()

	def setUp(self):
		for doctype in [*CATALOG_DOCTYPES, "ARCA Receiver VAT Condition Voucher Type", "ARCA Point of Sale"]:
			frappe.db.delete(doctype)
		self.client = FakeClient()
		patcher = patch.object(catalogs, "get_mtxca_client", return_value=self.client)
		patcher.start()
		self.addCleanup(patcher.stop)
		# Without wscdc in the certificate, unless a test says otherwise.
		self.wscdc = patch.object(catalogs, "get_wscdc_client", side_effect=ArcaError("no wscdc"))
		self.wscdc.start()
		self.addCleanup(self.wscdc.stop)

	def test_received_voucher_types_come_from_wscdc(self):
		self.wscdc.stop()
		received = MagicMock()
		received.voucher_types.return_value = [CD("11", "Factura C"), CD("1", "Factura A")]
		with patch.object(catalogs, "get_wscdc_client", return_value=received):
			catalogs.sync_catalogs(self.company)
		self.assertEqual(frappe.db.get_value("ARCA Voucher Type", "11", "description"), "Factura C")
		self.wscdc.start()

		# Without WSCDC the received types stay enabled.
		catalogs.sync_catalogs(self.company)
		self.assertEqual(frappe.db.get_value("ARCA Voucher Type", "11", "disabled"), 0)

	def test_first_sync_adds_everything(self):
		results = catalogs.sync_catalogs(self.company)

		self.assertEqual(results["ARCA Voucher Type"].added, 3)
		self.assertEqual(results["ARCA Currency"].added, 2)
		self.assertEqual(frappe.db.get_value("ARCA Currency", "DOL", "description"), "Dólar Estadounidense")
		self.assertEqual(frappe.db.get_value("ARCA VAT Condition", "5", "rate"), 21)

	def test_receiver_conditions_keep_their_voucher_types(self):
		catalogs.sync_catalogs(self.company)

		self.assertNotIn("88", self.client.receiver_calls)
		final_consumer = frappe.get_doc("ARCA Receiver VAT Condition", "5")
		self.assertEqual([row.voucher_type for row in final_consumer.voucher_types], ["6"])

	def test_resync_updates_and_disables(self):
		catalogs.sync_catalogs(self.company)
		self.client.answers["currencies"] = [CD("PES", "Peso Argentino")]

		result = catalogs.sync_catalogs(self.company)["ARCA Currency"]

		self.assertEqual((result.added, result.updated, result.disabled), (0, 1, 1))
		self.assertEqual(frappe.db.get_value("ARCA Currency", "PES", "description"), "Peso Argentino")
		self.assertEqual(frappe.db.get_value("ARCA Currency", "DOL", "disabled"), 1)

	def test_unchanged_resync_touches_nothing(self):
		catalogs.sync_catalogs(self.company)
		results = catalogs.sync_catalogs(self.company)
		for doctype, result in results.items():
			self.assertEqual(result.as_dict(), {"added": 0, "updated": 0, "disabled": 0}, doctype)

	def test_code_that_returns_is_enabled_again(self):
		catalogs.sync_catalogs(self.company)
		frappe.db.set_value("ARCA Currency", "DOL", "disabled", 1)
		catalogs.sync_catalogs(self.company)
		self.assertEqual(frappe.db.get_value("ARCA Currency", "DOL", "disabled"), 0)

	def test_other_mtxca_errors_are_not_swallowed(self):
		self.client.receivers = {}
		self.client.receiver_vat_conditions = lambda vt: (_ for _ in ()).throw(MtxcaError([CD("600", "x")]))
		with self.assertRaises(MtxcaError):
			catalogs.sync_catalogs(self.company)

	def test_points_of_sale_are_per_company_and_environment(self):
		result = catalogs.sync_points_of_sale(self.company)
		self.assertEqual(result.added, 2)

		point = frappe.get_doc("ARCA Point of Sale", f"{self.company}-Homologation-00004")
		self.assertEqual((point.number, point.blocked, str(point.deactivated_on)), (4, 1, "2026-01-31"))

		self.client.points = [PointOfSale(3, False, None)]
		self.assertEqual(catalogs.sync_points_of_sale(self.company).disabled, 1)

	def test_seeds_fill_only_missing_codes(self):
		catalogs.sync_catalogs(self.company)
		catalogs.seed_catalogs()
		catalogs.seed_catalogs()

		self.assertEqual(frappe.db.get_value("ARCA Voucher Type", "1", "description"), "Factura A")
		self.assertTrue(frappe.db.exists("ARCA Voucher Type", "51"))
		self.assertEqual(frappe.db.count("ARCA VAT Condition"), 6)
		self.assertEqual(frappe.db.get_value("ARCA VAT Condition", "4", "rate"), 10.5)

	def test_sync_now_requires_system_manager(self):
		with self.set_user("Guest"), self.assertRaises(frappe.PermissionError):
			catalogs.sync_now(self.company)

	def test_sync_now_reports_counts(self):
		result = catalogs.sync_now(self.company)
		self.assertEqual(result["ARCA Point of Sale"], {"added": 2, "updated": 0, "disabled": 0})
		self.assertEqual(result["ARCA Document Type"]["added"], 2)
