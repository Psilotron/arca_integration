from pathlib import Path

from frappe.tests import UnitTestCase

from arca_integration.services.errors import ServiceFault
from arca_integration.services.mtxca.client import Auth
from arca_integration.services.padron import PadronClient, Tax, Taxpayer, TaxpayerNotFound, parse_taxpayer
from arca_integration.services.soap import HttpResponse, SoapTransport, parse_xml

FIXTURES = Path(__file__).parent / "fixtures" / "padron"


def fixture(name: str) -> bytes:
	return (FIXTURES / f"{name}.xml").read_bytes()


def parse(name: str) -> Taxpayer:
	return parse_taxpayer(parse_xml(fixture(name))[0][0])


def client(name: str, calls=None, status=200) -> PadronClient:
	def post(url, body, headers, timeout, verify):
		if calls is not None:
			calls.append(body.decode())
		return HttpResponse(status, fixture(name))

	return PadronClient("https://padron.test", SoapTransport(post=post), Auth("TOK", "SIG", "30000000007"))


class TestPadron(UnitTestCase):
	def test_company(self):
		t = parse("persona_juridica")
		self.assertEqual(
			(t.cuit, t.name, t.is_company, t.is_active), ("30500001735", "BANCO PRUEBA 02", True, True)
		)
		self.assertEqual(t.document_type, "80")
		self.assertEqual(t.suggested_vat_condition, "1")
		self.assertEqual(
			(t.address.line1, t.address.city, t.address.province, t.address.postal_code),
			("AV. CORRIENTES 1234", "CAPITAL FEDERAL", "CIUDAD DE BUENOS AIRES", "1043"),
		)
		self.assertEqual(t.activities, ("Servicios financieros",))

	def test_person_without_taxes(self):
		t = parse("persona_fisica")
		self.assertEqual((t.name, t.is_company, t.document_type), ("DELIA BEATRIZ ELISA", False, "87"))
		self.assertEqual(t.suggested_vat_condition, "5")
		self.assertIsNone(t.address)

	def test_blocked_constancia_keeps_the_name(self):
		t = parse("persona_error")
		self.assertEqual(t.cuit, "20222222223")
		self.assertEqual(t.name, "VUGI EKOPO TPST")
		self.assertIn("datos biométricos", t.constancia_error)

	def test_vat_condition_suggestions(self):
		def taxpayer(*taxes):
			return Taxpayer(cuit="20000000001", taxes=tuple(Tax(*t) for t in taxes))

		self.assertEqual(taxpayer(("20", "MONOTRIBUTO", "ACTIVO")).suggested_vat_condition, "6")
		self.assertEqual(taxpayer(("32", "IVA EXENTO", "ACTIVO")).suggested_vat_condition, "4")
		self.assertEqual(taxpayer(("30", "IVA", "BAJA DEFINITIVA")).suggested_vat_condition, "5")

	def test_request(self):
		calls = []
		client("persona_juridica", calls).get_taxpayer("30-50000173-5")
		self.assertIn("<per:getPersona><sign>SIG</sign><token>TOK</token>", calls[0])
		self.assertIn(
			"<cuitRepresentada>30000000007</cuitRepresentada><idPersona>30500001735</idPersona>", calls[0]
		)

	def test_not_found(self):
		with self.assertRaises(TaxpayerNotFound):
			client("persona_fault", status=500).get_taxpayer("20000000001")

	def test_other_faults_are_not_hidden(self):
		fault = b"""<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault>
		<faultcode>soap:Server</faultcode><faultstring>ValidacionDeToken: No apareci\xc3\xb3 CUIT en lista de relaciones</faultstring>
		</soap:Fault></soap:Body></soap:Envelope>"""
		transport = SoapTransport(post=lambda *a: HttpResponse(500, fault))
		with self.assertRaises(ServiceFault):
			PadronClient("https://padron.test", transport, Auth("T", "S", "30000000007")).get_taxpayer(
				"20000000001"
			)
