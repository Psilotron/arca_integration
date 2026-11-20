from datetime import date

from frappe.tests import UnitTestCase

from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.client import Auth, CodeDescription, MtxcaClient, MtxcaError
from arca_integration.services.soap import HttpResponse, SoapTransport

ENVELOPE = """<?xml version="1.0"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ser="http://impl.service.wsmtxca.afip.gob.ar/service/"><soapenv:Body>{}</soapenv:Body></soapenv:Envelope>"""

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
