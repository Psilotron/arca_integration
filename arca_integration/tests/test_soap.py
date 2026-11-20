from frappe.tests import UnitTestCase

from arca_integration.services.errors import ServiceFault, TransportError, TransportTimeout
from arca_integration.services.soap import HttpResponse, SoapTransport, build_envelope, find_text, parse_xml

OK_BODY = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
<soap:Body><ns:pingResponse xmlns:ns="urn:test"><ns:value>pong</ns:value></ns:pingResponse></soap:Body>
</soap:Envelope>"""

FAULT_BODY = b"""<?xml version="1.0"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
<soapenv:Body><soapenv:Fault><faultcode xmlns:ns1="urn:x">ns1:coe.alreadyAuthenticated</faultcode>
<faultstring>El CEE ya posee un TA valido para el acceso al WSN solicitado</faultstring></soapenv:Fault></soapenv:Body>
</soapenv:Envelope>"""


def fake_post(status, content, calls=None):
	def post(url, body, headers, timeout, verify):
		if calls is not None:
			calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout, "verify": verify})
		return HttpResponse(status, content)

	return post


class TestSoap(UnitTestCase):
	def test_returns_first_body_element(self):
		calls = []
		transport = SoapTransport(timeout=5, verify="/ca.pem", post=fake_post(200, OK_BODY, calls))

		result = transport.call("https://x.test/ws", "<t:ping/>", {"t": "urn:test"}, soap_action="ping")

		self.assertEqual(result.tag, "{urn:test}pingResponse")
		self.assertEqual(find_text(result, "value"), "pong")
		self.assertEqual(calls[0]["timeout"], 5)
		self.assertEqual(calls[0]["verify"], "/ca.pem")
		self.assertEqual(calls[0]["headers"]["SOAPAction"], '"ping"')
		self.assertIn(b'xmlns:t="urn:test"', calls[0]["body"])

	def test_fault_with_http_500(self):
		transport = SoapTransport(post=fake_post(500, FAULT_BODY))
		with self.assertRaises(ServiceFault) as ctx:
			transport.call("https://x.test/ws", "<t:ping/>", {"t": "urn:test"})
		self.assertEqual(ctx.exception.code, "ns1:coe.alreadyAuthenticated")
		self.assertIn("TA valido", ctx.exception.message)

	def test_http_error_without_xml(self):
		transport = SoapTransport(post=fake_post(502, b"<html>Bad Gateway"))
		with self.assertRaises(TransportError) as ctx:
			transport.call("https://x.test/ws", "<t:ping/>", {"t": "urn:test"})
		self.assertIn("502", ctx.exception.message)

	def test_timeout_is_distinguishable(self):
		def post(*args):
			raise TransportTimeout("ARCA did not answer in time")

		with self.assertRaises(TransportTimeout):
			SoapTransport(post=post).call("https://x.test/ws", "<t:ping/>", {"t": "urn:test"})

	def test_entities_are_not_expanded(self):
		evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
		self.assertNotIn("root:", parse_xml(evil).xpath("string(.)"))

	def test_envelope(self):
		envelope = parse_xml(build_envelope("<a:b>1</a:b>", {"a": "urn:a"}))
		self.assertEqual(find_text(envelope, "b"), "1")
