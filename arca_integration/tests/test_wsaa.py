from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape

from frappe.tests import UnitTestCase

from arca_integration.services.credentials import Credentials, generate_private_key
from arca_integration.services.errors import AlreadyAuthenticated, AuthError
from arca_integration.services.soap import HttpResponse, SoapTransport, find_text, parse_xml
from arca_integration.services.wsaa import (
	Ticket,
	build_login_ticket_request,
	get_ticket,
	login,
	parse_login_response,
	sign_cms,
)
from arca_integration.tests.test_credentials import make_certificate
from arca_integration.tests.test_soap import FAULT_BODY

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)

TICKET_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<loginTicketResponse version="1.0"><header><source>CN=wsaahomo</source><destination>SERIALNUMBER=CUIT 30000000007, CN=test</destination>
<uniqueId>1</uniqueId><generationTime>2026-11-20T12:00:00.000-03:00</generationTime>
<expirationTime>2026-11-21T00:00:00.000-03:00</expirationTime></header>
<credentials><token>TOKEN==</token><sign>SIGN==</sign></credentials></loginTicketResponse>"""

LOGIN_RESPONSE = f"""<?xml version="1.0"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>
<loginCmsResponse xmlns="http://wsaa.view.sua.dvadac.desein.afip.gov"><loginCmsReturn>{escape(TICKET_XML)}</loginCmsReturn></loginCmsResponse>
</soapenv:Body></soapenv:Envelope>""".encode()


def credentials() -> Credentials:
	key = generate_private_key()
	return Credentials(cuit="30000000007", certificate=make_certificate(key), private_key=key)


class MemoryStore:
	def __init__(self, ticket=None):
		self.ticket = ticket
		self.writes = 0

	def get(self):
		return self.ticket

	def set(self, ticket):
		self.ticket = ticket
		self.writes += 1


class TestWsaa(UnitTestCase):
	def test_login_ticket_request(self):
		xml = parse_xml(build_login_ticket_request("wsmtxca", NOW, unique_id=7))
		self.assertEqual(find_text(xml, "service"), "wsmtxca")
		self.assertEqual(find_text(xml, "uniqueId"), "7")
		self.assertEqual(find_text(xml, "generationTime"), "2026-11-20T14:50:00+00:00")
		self.assertEqual(find_text(xml, "expirationTime"), "2026-11-20T15:10:00+00:00")

	def test_cms_carries_the_request(self):
		request = build_login_ticket_request("wsmtxca", NOW)
		self.assertIn(request.encode(), sign_cms(request, credentials()))

	def test_parse_login_response(self):
		ticket = parse_login_response(parse_xml(LOGIN_RESPONSE)[0][0])
		self.assertEqual((ticket.token, ticket.sign), ("TOKEN==", "SIGN=="))
		self.assertEqual(ticket.expires_at, datetime(2026, 11, 21, 3, 0, tzinfo=UTC))

	def test_login(self):
		calls = []

		def post(url, body, headers, timeout, verify):
			calls.append(body)
			return HttpResponse(200, LOGIN_RESPONSE)

		ticket = login(credentials(), "wsmtxca", "https://wsaa.test", SoapTransport(post=post), NOW)
		self.assertEqual(ticket.token, "TOKEN==")
		self.assertIn(b"<wsaa:loginCms><wsaa:in0>", calls[0])

	def test_already_authenticated(self):
		transport = SoapTransport(post=lambda *a: HttpResponse(500, FAULT_BODY))
		with self.assertRaises(AlreadyAuthenticated):
			login(credentials(), "wsmtxca", "https://wsaa.test", transport, NOW)

	def test_incomplete_ticket(self):
		broken = LOGIN_RESPONSE.replace(b"&lt;sign&gt;SIGN==&lt;/sign&gt;", b"")
		with self.assertRaises(AuthError):
			parse_login_response(parse_xml(broken)[0][0])


class TestGetTicket(UnitTestCase):
	def setUp(self):
		self.locks = 0
		self.logins = 0

	@contextmanager
	def lock(self):
		self.locks += 1
		yield

	def do_login(self):
		self.logins += 1
		return Ticket("new", "new", NOW + timedelta(hours=12))

	def test_valid_ticket_is_reused_without_lock(self):
		store = MemoryStore(Ticket("old", "old", NOW + timedelta(hours=1)))
		self.assertEqual(get_ticket(store, self.lock, self.do_login, NOW).token, "old")
		self.assertEqual((self.locks, self.logins, store.writes), (0, 0, 0))

	def test_ticket_near_expiry_is_renewed(self):
		store = MemoryStore(Ticket("old", "old", NOW + timedelta(minutes=5)))
		self.assertEqual(get_ticket(store, self.lock, self.do_login, NOW).token, "new")
		self.assertEqual((self.locks, self.logins, store.writes), (1, 1, 1))

	def test_ticket_stored_by_another_process_while_waiting(self):
		store = MemoryStore()

		@contextmanager
		def lock_then_other_process_logs_in():
			store.ticket = Ticket("other", "other", NOW + timedelta(hours=12))
			yield

		ticket = get_ticket(store, lock_then_other_process_logs_in, self.do_login, NOW)
		self.assertEqual((ticket.token, self.logins), ("other", 0))
