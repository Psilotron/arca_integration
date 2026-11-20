from frappe.tests import UnitTestCase

from arca_integration.services.mtxca.catalogs import group_receiver_conditions, is_issuable, vat_rate
from arca_integration.services.mtxca.client import CodeDescription as CD


class TestMtxcaCatalogs(UnitTestCase):
	def test_vat_rate(self):
		self.assertEqual(vat_rate("10.5%"), 10.5)
		self.assertEqual(vat_rate("21%"), 21.0)
		self.assertEqual(vat_rate("0%"), 0.0)
		self.assertIsNone(vat_rate("Exento"))

	def test_associated_only_voucher_types_are_not_issuable(self):
		self.assertTrue(is_issuable(CD("1", "Factura A")))
		self.assertFalse(
			is_issuable(
				CD("88", "Remito Electrónico de Tabaco Acondicionado (sólo \npara comprobantes asociados)")
			)
		)

	def test_group_receiver_conditions(self):
		grouped = group_receiver_conditions(
			{
				"6": [CD("5", "Consumidor Final"), CD("4", "IVA Sujeto Exento")],
				"1": [CD("1", "IVA Responsable Inscripto")],
				"8": [CD("5", "Consumidor Final")],
			}
		)
		self.assertEqual(
			[(c.code, c.voucher_types) for c in grouped], [("1", ("1",)), ("4", ("6",)), ("5", ("6", "8"))]
		)
