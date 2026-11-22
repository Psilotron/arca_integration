import base64
import json
from datetime import date
from decimal import Decimal as D

from frappe.tests import UnitTestCase

from arca_integration.services import qr

# The example in ARCA's "Especificaciones del QR incluido en las facturas electrónicas".
EXAMPLE = {
	"issue_date": date(2020, 10, 13),
	"cuit": "30000000007",
	"point_of_sale": 10,
	"voucher_type": 1,
	"number": 94,
	"total": D("12100.00"),
	"currency": "DOL",
	"exchange_rate": D("65.000000"),
	"document_type": "80",
	"document_number": "20000000001",
	"cae": "70417054367476",
}
PUBLISHED = (
	"eyJ2ZXIiOjEsImZlY2hhIjoiMjAyMC0xMC0xMyIsImN1aXQiOjMwMDAwMDAwMDA3LCJwdG9WdGEiOjEwLCJ0aXBvQ21wIjoxLCJu"
	"cm9DbXAiOjk0LCJpbXBvcnRlIjoxMjEwMCwibW9uZWRhIjoiRE9MIiwiY3R6Ijo2NSwidGlwb0RvY1JlYyI6ODAsIm5yb0RvY1Jl"
	"YyI6MjAwMDAwMDAwMDEsInRpcG9Db2RBdXQiOiJFIiwiY29kQXV0Ijo3MDQxNzA1NDM2NzQ3Nn0="
)


class TestQr(UnitTestCase):
	def test_official_example(self):
		self.assertEqual(qr.text(qr.payload(**EXAMPLE)), f"https://www.arca.gob.ar/fe/qr/?p={PUBLISHED}")

	def test_without_receiver_document(self):
		data = qr.payload(**{**EXAMPLE, "document_type": "", "document_number": ""})
		self.assertNotIn("tipoDocRec", data)
		self.assertEqual(list(data)[-2:], ["tipoCodAut", "codAut"])

	def test_decimals_are_kept(self):
		encoded = qr.text(qr.payload(**{**EXAMPLE, "total": D("121.50"), "exchange_rate": D(1)}))
		data = json.loads(base64.b64decode(encoded.split("?p=")[1]))
		self.assertEqual((data["importe"], data["ctz"]), (121.5, 1))

	def test_svg(self):
		self.assertTrue(qr.svg("https://www.arca.gob.ar/fe/qr/?p=x").lstrip().startswith("<svg"))


class TestParse(UnitTestCase):
	"""Reading the QR of a received voucher."""

	def test_official_example(self):
		voucher = qr.parse(f"https://www.afip.gob.ar/fe/qr/?p={PUBLISHED}")
		self.assertEqual(
			(voucher.issuer_cuit, voucher.point_of_sale, voucher.voucher_type, voucher.number),
			("30000000007", 10, 1, 94),
		)
		self.assertEqual(
			(voucher.issue_date, voucher.total, voucher.currency), (date(2020, 10, 13), D(12100), "DOL")
		)
		self.assertEqual((voucher.authorization_mode, voucher.authorization_code), ("CAE", "70417054367476"))
		self.assertEqual(
			(voucher.receiver_document_type, voucher.receiver_document_number), ("80", "20000000001")
		)

	def test_what_we_print_reads_back(self):
		voucher = qr.parse(qr.text(qr.payload(**EXAMPLE)))
		self.assertEqual(voucher.total, D(12100))
		self.assertEqual(voucher.exchange_rate, D(65))

	def test_pasted_links(self):
		for text in (
			PUBLISHED,  # just the payload
			f"  https://www.arca.gob.ar/fe/qr/?p={PUBLISHED.rstrip('=')}  ",  # no padding
			f"https://www.arca.gob.ar/fe/qr/?p={PUBLISHED.replace('+', '%2B').replace('=', '%3D')}",
		):
			with self.subTest(text[:30]):
				self.assertEqual(qr.parse(text).number, 94)

	def test_caea_and_strings(self):
		data = {"ver": 1, "fecha": "2026-11-20", "cuit": "20111111112", "ptoVta": "3", "tipoCmp": "11",
			"nroCmp": "15", "importe": "1500.50", "moneda": "PES", "ctz": "1", "tipoCodAut": "A",
			"codAut": "12345678901234"}  # fmt: skip
		voucher = qr.parse(base64.b64encode(json.dumps(data).encode()).decode())
		self.assertEqual(
			(voucher.authorization_mode, voucher.voucher_type, voucher.total), ("CAEA", 11, D("1500.50"))
		)
		self.assertEqual(voucher.receiver_document_number, "")

	def test_not_a_voucher(self):
		for text in ("", "https://example.com", "https://www.arca.gob.ar/fe/qr/?p=bm9wZQ==", "%%%"):
			with self.subTest(text), self.assertRaises(qr.InvalidQr):
				qr.parse(text)
