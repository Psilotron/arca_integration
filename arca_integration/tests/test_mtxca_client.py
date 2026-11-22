from datetime import date
from decimal import Decimal as D
from pathlib import Path

from frappe.tests import UnitTestCase

from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.client import MTXCA_NS, Auth, CodeDescription, MtxcaClient, MtxcaError
from arca_integration.services.soap import HttpResponse, SoapTransport, parse_xml

# Downloaded from ARCA (homologation; production declares the same namespace).
WSDL = Path(__file__).parent / "fixtures" / "mtxca" / "MTXCAService.wsdl"
XSD = "{http://www.w3.org/2001/XMLSchema}"

ENVELOPE = """<?xml version="1.0"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ser="http://impl.service.wsmtxca.afip.gov.ar/service/"><soapenv:Body>{}</soapenv:Body></soapenv:Envelope>"""

# Examples from the WSMTXCA manual v0.25.10, p. 268 and 288.
VOUCHER_TYPES = ENVELOPE.format(
	"""<ser:consultarTiposComprobanteResponse><arrayTiposComprobante>
	<codigoDescripcion><codigo>1</codigo><descripcion>Factura A</descripcion></codigoDescripcion>
	<codigoDescripcion><codigo>6</codigo><descripcion>Factura B</descripcion></codigoDescripcion>
	</arrayTiposComprobante>
	<evento><codigo>12</codigo><descripcion>Mantenimiento programado</descripcion></evento>
	</ser:consultarTiposComprobanteResponse>"""
)
RECEIVER_ERROR = ENVELOPE.format(
	"""<ser:consultarCondicionesIVAReceptorResponse><arrayErrores>
	<codigoDescripcion><codigo>196</codigo><descripcion>Tipo de comprobante invalido</descripcion></codigoDescripcion>
	</arrayErrores></ser:consultarCondicionesIVAReceptorResponse>"""
)
POINTS_OF_SALE = ENVELOPE.format(
	"""<ser:consultarPuntosVentaCAEResponse><arrayPuntosVenta>
	<puntoVenta><numeroPuntoVenta>123</numeroPuntoVenta><bloqueado>Si</bloqueado><fechaBaja>2026-01-31</fechaBaja></puntoVenta>
	<puntoVenta><numeroPuntoVenta>3</numeroPuntoVenta><bloqueado>N</bloqueado></puntoVenta>
	</arrayPuntosVenta></ser:consultarPuntosVentaCAEResponse>"""
)
DUMMY = ENVELOPE.format(
	"<ser:dummyResponse><appserver>OK</appserver><authserver>OK</authserver><dbserver>OK</dbserver></ser:dummyResponse>"
)


AUTH = Auth("TOK", "SIG", "30000000007")


def client(response: str, calls=None, auth=AUTH):
	def post(url, body, headers, timeout, verify):
		if calls is not None:
			calls.append({"body": body.decode(), "headers": headers})
		return HttpResponse(200, response.encode())

	return MtxcaClient("https://mtxca.test", SoapTransport(post=post), auth)


class TestMtxcaClient(UnitTestCase):
	def test_catalog_and_auth_request(self):
		calls = []
		c = client(VOUCHER_TYPES, calls)

		self.assertEqual(
			c.voucher_types(), [CodeDescription("1", "Factura A"), CodeDescription("6", "Factura B")]
		)
		body = calls[0]["body"]
		self.assertIn("<ser:consultarTiposComprobanteRequest><authRequest><token>TOK</token>", body)
		self.assertIn("<cuitRepresentada>30000000007</cuitRepresentada>", body)
		self.assertEqual(calls[0]["headers"]["SOAPAction"], '"consultarTiposComprobante"')

	def test_events_are_collected(self):
		c = client(VOUCHER_TYPES)
		c.voucher_types()
		self.assertEqual(c.events, [CodeDescription("12", "Mantenimiento programado")])

	def test_business_errors_raise(self):
		calls = []
		with self.assertRaises(MtxcaError) as ctx:
			client(RECEIVER_ERROR, calls).receiver_vat_conditions(88)
		self.assertEqual(ctx.exception.codes, {"196"})
		self.assertIn("<codigoTipoComprobante>88</codigoTipoComprobante>", calls[0]["body"])

	def test_points_of_sale(self):
		points = client(POINTS_OF_SALE).points_of_sale_cae()
		self.assertEqual(
			[(p.number, p.blocked, p.deactivated_on) for p in points],
			[(123, True, date(2026, 1, 31)), (3, False, None)],
		)

	def test_auth_is_escaped(self):
		calls = []
		client(VOUCHER_TYPES, calls, Auth("a<b&c", "SIG", "30000000007")).voucher_types()
		self.assertIn("<token>a&lt;b&amp;c</token>", calls[0]["body"])

	def test_dummy_needs_no_auth(self):
		status = client(DUMMY, auth=None).dummy()
		self.assertEqual((status.app_server, status.db_server, status.auth_server), ("OK", "OK", "OK"))

	def test_operations_need_auth(self):
		with self.assertRaises(ArcaError):
			client(VOUCHER_TYPES, auth=None).voucher_types()


class TestMtxcaContract(UnitTestCase):
	"""Requests built by the client must match ARCA's published WSDL."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.schema = parse_xml(WSDL.read_bytes()).find(f".//{XSD}schema")
		cls.types = {t.get("name"): t for t in cls.schema.iter(f"{XSD}complexType")}
		cls.elements = {
			e.get("name"): e.get("type").split(":")[-1] for e in cls.schema.findall(f"{XSD}element")
		}

	def children(self, type_name: str) -> list[str]:
		return [e.get("name") for e in self.types[type_name].iter(f"{XSD}element")]

	def test_namespace(self):
		self.assertEqual(self.schema.get("targetNamespace"), MTXCA_NS)

	def test_requests_match_schema(self):
		operations = {
			"voucher_types": (),
			"document_types": (),
			"vat_conditions": (),
			"currencies": (),
			"units_of_measure": (),
			"tax_types": (),
			"receiver_vat_conditions": (1,),
			"points_of_sale_cae": (),
			"last_authorized": (1, 1),
			"get_voucher": (1, 1, 1),
			"exchange_rate": ("DOL", date(2026, 11, 20)),
		}
		for method, args in operations.items():
			with self.subTest(method):
				calls = []
				getattr(client(ENVELOPE.format("<ser:response/>"), calls), method)(*args)
				request = parse_xml(calls[0]["body"].encode()).find(".//{*}Body")[0]
				namespace, name = request.tag[1:].split("}")
				self.assertEqual(namespace, self.schema.get("targetNamespace"))
				self.assertIn(name, self.elements)
				expected = self.children(self.elements[name])
				self.assertEqual([child.tag for child in request], expected[: len(request)])
				for child in request:
					if child.tag == "authRequest":
						self.assertEqual([c.tag for c in child], self.children("AuthRequestType"))


AUTHORIZED = ENVELOPE.format(
	"""<ser:autorizarComprobanteResponse><resultado>O</resultado><comprobanteResponse>
	<cuit>30000000007</cuit><codigoTipoComprobante>1</codigoTipoComprobante><numeroPuntoVenta>4000</numeroPuntoVenta>
	<numeroComprobante>1</numeroComprobante><fechaEmision>2010-11-01</fechaEmision><CAE>12345678901234</CAE>
	<fechaVencimientoCAE>2010-11-16</fechaVencimientoCAE></comprobanteResponse>
	<arrayObservaciones><codigoDescripcion><codigo>504</codigo><descripcion>GTIN no registrado</descripcion></codigoDescripcion></arrayObservaciones>
	</ser:autorizarComprobanteResponse>"""
)
REJECTED = ENVELOPE.format(
	"""<ser:autorizarComprobanteResponse><resultado>R</resultado><arrayErrores>
	<codigoDescripcion><codigo>102</codigo><descripcion>Numero invalido</descripcion></codigoDescripcion>
	</arrayErrores></ser:autorizarComprobanteResponse>"""
)
LAST_NONE = ENVELOPE.format("<ser:consultarUltimoComprobanteAutorizadoResponse/>")
LAST = ENVELOPE.format(
	"<ser:consultarUltimoComprobanteAutorizadoResponse><numeroComprobante>124</numeroComprobante>"
	"</ser:consultarUltimoComprobanteAutorizadoResponse>"
)
LAST_FIRST_OF_ITS_TYPE = ENVELOPE.format(
	"""<ser:consultarUltimoComprobanteAutorizadoResponse><arrayErrores><codigoDescripcion><codigo>1502</codigo>
	<descripcion>Para la CUIT, Tipo de Comprobante y Punto de Ventas requeridos no se registran comprobantes en las bases del Organismo</descripcion>
	</codigoDescripcion></arrayErrores></ser:consultarUltimoComprobanteAutorizadoResponse>"""
)
NOT_FOUND = ENVELOPE.format(
	"""<ser:consultarComprobanteResponse><arrayErrores><codigoDescripcion><codigo>1503</codigo>
	<descripcion>No existe</descripcion></codigoDescripcion></arrayErrores></ser:consultarComprobanteResponse>"""
)
FOUND = ENVELOPE.format(
	"""<ser:consultarComprobanteResponse><comprobante><codigoTipoComprobante>1</codigoTipoComprobante>
	<numeroPuntoVenta>4000</numeroPuntoVenta><numeroComprobante>7</numeroComprobante><fechaEmision>2010-11-01</fechaEmision>
	<codigoAutorizacion>12345678901234</codigoAutorizacion><fechaVencimiento>2010-11-16</fechaVencimiento>
	<codigoTipoDocumento>80</codigoTipoDocumento><numeroDocumento>30000000007</numeroDocumento>
	<importeSubtotal>100.00</importeSubtotal><importeTotal>122.00</importeTotal><codigoMoneda>PES</codigoMoneda>
	</comprobante></ser:consultarComprobanteResponse>"""
)

RATE = ENVELOPE.format(
	"<ser:consultarCotizacionMonedaResponse><cotizacionMoneda>1181.524</cotizacionMoneda>"
	"</ser:consultarCotizacionMonedaResponse>"
)
NO_RATE = ENVELOPE.format("<ser:consultarCotizacionMonedaResponse/>")


def manual_invoice_a():
	from dataclasses import replace

	from arca_integration.services.mtxca.builder import review
	from arca_integration.tests.test_mtxca_builder import RULES, draft

	return replace(review(draft(), RULES).voucher, number=1)


class TestEmissionOperations(UnitTestCase):
	def test_authorize(self):
		calls = []
		authorization = client(AUTHORIZED, calls).authorize(manual_invoice_a())

		self.assertEqual(
			(authorization.result, authorization.cae, authorization.number),
			("O", "12345678901234", 1),
		)
		self.assertEqual(authorization.cae_due_date, date(2010, 11, 16))
		self.assertEqual([o.code for o in authorization.observations], ["504"])
		body = calls[0]["body"]
		self.assertIn("<importeTotal>122.00</importeTotal>", body)
		self.assertIn("<precioUnitario>100</precioUnitario>", body)

	def test_rejection_raises(self):
		with self.assertRaises(MtxcaError) as ctx:
			client(REJECTED).authorize(manual_invoice_a())
		self.assertEqual(ctx.exception.codes, {"102"})

	def test_last_authorized(self):
		self.assertEqual(client(LAST_NONE).last_authorized(1, 4000), 0)
		self.assertEqual(client(LAST).last_authorized(1, 4000), 124)
		# ARCA answers 1502 before the first voucher of a type, e.g. the first credit note.
		self.assertEqual(client(LAST_FIRST_OF_ITS_TYPE).last_authorized(3, 4000), 0)

	def test_get_voucher(self):
		self.assertIsNone(client(NOT_FOUND).get_voucher(1, 4000, 7))
		voucher = client(FOUND).get_voucher(1, 4000, 7)
		self.assertEqual((voucher.number, voucher.cae, str(voucher.total)), (7, "12345678901234", "122.00"))

	def test_exchange_rate(self):
		calls = []
		rate = client(RATE, calls).exchange_rate("DOL", date(2026, 11, 20))
		self.assertEqual(rate, D("1181.524"))
		self.assertIn("<fechaCotizacion>2026-11-20</fechaCotizacion>", calls[0]["body"])
		self.assertIsNone(client(NO_RATE).exchange_rate("DOL", date(2026, 12, 1)))

	def test_exchanges_hide_the_ticket(self):
		c = client(LAST)
		c.last_authorized(1, 4000)
		self.assertIn("<token>***</token>", c.exchanges[0].request)
		self.assertNotIn("TOK", c.exchanges[0].request)
		self.assertIn("124", c.exchanges[0].response)


class TestVoucherContract(TestMtxcaContract):
	def test_voucher_follows_the_schema(self):
		calls = []
		client(AUTHORIZED, calls).authorize(manual_invoice_a())
		request = parse_xml(calls[0]["body"].encode()).find(".//{*}Body")[0]
		self.assertEqual([c.tag for c in request], self.children("AutorizarComprobanteRequestType"))

		voucher = request.find("comprobanteCAERequest")
		declared = self.direct_children("ComprobanteType")
		tags = [c.tag for c in voucher]
		self.assertEqual(tags, [name for name in declared if name in tags])
		item = voucher.find("arrayItems/item")
		item_declared = self.direct_children("ItemType")
		self.assertEqual([c.tag for c in item], [n for n in item_declared if n in {c.tag for c in item}])

	def direct_children(self, type_name):
		sequence = self.types[type_name].find(f"{XSD}sequence")
		return [e.get("name") for e in sequence.findall(f"{XSD}element")]


class TestRequestsValidateAgainstXsd(UnitTestCase):
	"""Validate full requests (names, order and values) with the XSD inside ARCA's WSDL."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from lxml import etree

		schema = parse_xml(WSDL.read_bytes()).find(f".//{XSD}schema")
		# The schema uses the WSDL's `tns` prefix: declare it on the standalone copy.
		text = etree.tostring(schema, encoding="unicode")
		if "xmlns:tns=" not in text.split(">", 1)[0]:
			namespace = schema.get("targetNamespace")
			text = text.replace("<xsd:schema ", f'<xsd:schema xmlns:tns="{namespace}" ', 1)
		cls.xsd = etree.XMLSchema(etree.fromstring(text))

	def assertValid(self, voucher):
		calls = []
		client(AUTHORIZED, calls).authorize(voucher)
		request = parse_xml(calls[0]["body"].encode()).find(".//{*}Body")[0]
		self.assertTrue(self.xsd.validate(request), self.xsd.error_log)

	def test_invoice_a_without_other_taxes(self):
		from dataclasses import replace

		from arca_integration.services.mtxca.builder import review
		from arca_integration.tests.test_mtxca_builder import RULES, draft, vat

		voucher = review(draft(taxes=(vat("21"),), grand_total=D("121")), RULES).voucher
		self.assertValid(replace(voucher, number=2))

	def test_credit_note_a(self):
		from dataclasses import replace

		from arca_integration.services.mtxca.builder import review
		from arca_integration.services.mtxca.voucher import AssociatedVoucher, Kind
		from arca_integration.tests.test_mtxca_builder import RULES, TODAY, draft

		invoice = AssociatedVoucher(1, 4000, 12, TODAY)
		voucher = review(draft(kind=Kind.CREDIT_NOTE, associated=invoice), RULES).voucher
		self.assertValid(replace(voucher, number=3))

	def test_invoice_a_with_other_taxes(self):
		self.assertValid(manual_invoice_a())

	def test_invoice_in_dollars_paid_in_dollars(self):
		from dataclasses import replace

		from arca_integration.services.mtxca.builder import review
		from arca_integration.tests.test_mtxca_builder import RULES, dollars

		voucher = review(dollars(paid_in_currency=True), RULES).voucher
		calls = []
		client(AUTHORIZED, calls).authorize(replace(voucher, number=4))
		self.assertIn(
			"<codigoMoneda>DOL</codigoMoneda><cotizacionMoneda>1181.524</cotizacionMoneda>"
			"<cancelaEnMismaMonedaExtranjera>S</cancelaEnMismaMonedaExtranjera>",
			calls[0]["body"],
		)
		self.assertValid(replace(voucher, number=4))

	def test_invoice_b_for_services(self):
		from dataclasses import replace

		from arca_integration.services.mtxca.builder import review
		from arca_integration.tests.test_mtxca_builder import FINAL_CONSUMER, RULES, draft, line, vat

		voucher = review(
			draft(
				receiver=FINAL_CONSUMER,
				lines=(line(1, "100", "5", stock=False, quantity=D(3)), line(2, "50", "2", stock=False)),
				taxes=(vat("21"),),
				grand_total=D("171"),
			),
			RULES,
		).voucher
		self.assertValid(replace(voucher, number=1))
