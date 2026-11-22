from datetime import date
from decimal import Decimal as D
from pathlib import Path

from frappe.tests import UnitTestCase

from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.client import Auth
from arca_integration.services.soap import HttpResponse, SoapTransport, parse_xml
from arca_integration.services.wscdc.client import (
	WSCDC_NS,
	ReceivedVoucher,
	WscdcClient,
	WscdcError,
	request_xml,
)

WSDL = Path(__file__).parent / "fixtures" / "wscdc" / "service.wsdl"
XSD = "{http://www.w3.org/2001/XMLSchema}"
ENVELOPE = (
	'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
	"<soap:Body>{}</soap:Body></soap:Envelope>"
)
AUTH = Auth("TOK", "SIG", "30000000007")


def result(body: str) -> str:
	return ENVELOPE.format(
		f'<ComprobanteConstatarResponse xmlns="{WSCDC_NS}"><ComprobanteConstatarResult>{body}'
		"</ComprobanteConstatarResult></ComprobanteConstatarResponse>"
	)


# Manual of 2019-12-01, p. 15 and 16: a CAE voucher verified.
APPROVED = result(
	"<CmpResp><CbteModo>CAE</CbteModo></CmpResp><Resultado>A</Resultado><FchProceso>20130729204436</FchProceso>"
)
REJECTED = result(
	"<Resultado>R</Resultado><Observaciones><Obs><Code>110</Code><Msg>Importe no coincide</Msg></Obs>"
	"<Obs><Code>107</Code><Msg>Fecha no coincide</Msg></Obs></Observaciones>"
)
BAD_FORMAT = result(
	"<Resultado>R</Resultado><Errors><Err><Code>10</Code><Msg>CodAutorizacion invalido</Msg></Err></Errors>"
)
NOT_AUTHORIZED = result(
	"<Errors><Err><Code>600</Code><Msg>No se corresponden token y firma</Msg></Err></Errors>"
)

VOUCHER = ReceivedVoucher(
	mode="CAE",
	issuer_cuit="20000000001",
	point_of_sale=1,
	voucher_type=1,
	number=2,
	issue_date=date(2010, 10, 14),
	total=D("300.8"),
	authorization_code="60428000005029",
	receiver_document_type="80",
	receiver_document_number="30000000007",
)


def client(response: str, calls=None) -> WscdcClient:
	def post(url, body, headers, timeout, verify):
		if calls is not None:
			calls.append({"body": body.decode(), "headers": headers})
		return HttpResponse(200, response.encode())

	return WscdcClient("https://wscdc.test", SoapTransport(post=post), AUTH)


class TestWscdcClient(UnitTestCase):
	def test_approved(self):
		calls = []
		verification = client(APPROVED, calls).verify(VOUCHER)
		self.assertTrue(verification.approved)
		self.assertEqual(verification.processed_at, "20130729204436")
		self.assertEqual(calls[0]["headers"]["SOAPAction"], f'"{WSCDC_NS}ComprobanteConstatar"')
		body = calls[0]["body"]
		for part in (
			"<CbteFch>20101014</CbteFch>",
			"<ImpTotal>300.80</ImpTotal>",
			"<DocNroReceptor>30000000007</DocNroReceptor>",
		):
			self.assertIn(part, body)

	def test_rejected_with_reasons(self):
		verification = client(REJECTED).verify(VOUCHER)
		self.assertFalse(verification.approved)
		self.assertEqual([o.code for o in verification.observations], ["110", "107"])

	def test_wrong_format_is_a_rejection(self):
		verification = client(BAD_FORMAT).verify(VOUCHER)
		self.assertEqual((verification.approved, verification.observations[0].code), (False, "10"))

	def test_authentication_errors_raise(self):
		with self.assertRaises(WscdcError) as ctx:
			client(NOT_AUTHORIZED).verify(VOUCHER)
		self.assertEqual(ctx.exception.codes, {"600"})

	def test_exchanges_hide_the_ticket(self):
		c = client(APPROVED)
		c.verify(VOUCHER)
		self.assertIn("<Token>***</Token>", c.exchanges[0].request)
		self.assertNotIn("TOK", c.exchanges[0].request)

	def test_needs_auth(self):
		with self.assertRaises(ArcaError):
			WscdcClient("https://wscdc.test", SoapTransport(post=None)).verify(VOUCHER)

	def test_voucher_types(self):
		response = ENVELOPE.format(
			f'<ComprobantesTipoConsultarResponse xmlns="{WSCDC_NS}"><ComprobantesTipoConsultarResult><ResultGet>'
			"<CbteTipo><Id>11</Id><Desc>Factura C</Desc></CbteTipo></ResultGet>"
			"</ComprobantesTipoConsultarResult></ComprobantesTipoConsultarResponse>"
		)
		self.assertEqual(
			[(t.code, t.description) for t in client(response).voucher_types()], [("11", "Factura C")]
		)


class TestWscdcContract(UnitTestCase):
	"""The request follows CmpDatos in ARCA's WSDL: names and order."""

	def test_request_follows_the_schema(self):
		schema = parse_xml(WSDL.read_bytes()).find(f".//{XSD}schema")
		cmp_datos = next(t for t in schema.iter(f"{XSD}complexType") if t.get("name") == "CmpDatos")
		expected = [e.get("name") for e in cmp_datos.iter(f"{XSD}element")]
		sent = [child.tag for child in parse_xml(request_xml(VOUCHER))]
		self.assertEqual(sent, [name for name in expected if name in sent])
		self.assertEqual(sent, expected[: len(sent)])
