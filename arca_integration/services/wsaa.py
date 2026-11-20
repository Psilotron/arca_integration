"""WSAA login: sign a LoginTicketRequest as CMS and exchange it for a ticket.

A ticket (token + sign) is valid for 12 hours, and WSAA refuses a new login for
the same certificate and service while one is valid
(`coe.alreadyAuthenticated`). Callers must therefore keep tickets in durable
storage and serialize logins; `get_ticket` does both through the `store` and
`lock` it receives.
"""

import base64
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

from .credentials import Credentials, normalize_pem
from .errors import AlreadyAuthenticated, AuthError, ServiceFault
from .soap import SoapTransport, find_text, parse_xml

WSAA_NS = "http://wsaa.view.sua.dvadac.desein.afip.gov"
REQUEST_WINDOW = timedelta(minutes=10)  # tolerance for clock skew with ARCA
TICKET_MARGIN = timedelta(minutes=10)  # stop using a ticket this long before it expires


@dataclass(frozen=True)
class Ticket:
	token: str
	sign: str
	expires_at: datetime  # timezone-aware

	def is_valid(self, now: datetime, margin: timedelta = TICKET_MARGIN) -> bool:
		return now < self.expires_at - margin


class TicketStore(Protocol):
	def get(self) -> Ticket | None: ...

	def set(self, ticket: Ticket) -> None: ...


def build_login_ticket_request(service: str, now: datetime, unique_id: int | None = None) -> str:
	unique_id = int(now.timestamp()) if unique_id is None else unique_id
	generation = (now - REQUEST_WINDOW).isoformat(timespec="seconds")
	expiration = (now + REQUEST_WINDOW).isoformat(timespec="seconds")
	return (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<loginTicketRequest version="1.0"><header>'
		f"<uniqueId>{unique_id}</uniqueId>"
		f"<generationTime>{generation}</generationTime>"
		f"<expirationTime>{expiration}</expirationTime>"
		f"</header><service>{escape(service)}</service></loginTicketRequest>"
	)


def sign_cms(content: str, credentials: Credentials) -> bytes:
	"""CMS SignedData (DER) with the content attached, as WSAA requires."""
	certificate = x509.load_pem_x509_certificate(normalize_pem(credentials.certificate).encode())
	private_key = serialization.load_pem_private_key(normalize_pem(credentials.private_key).encode(), None)
	return (
		pkcs7.PKCS7SignatureBuilder()
		.set_data(content.encode())
		.add_signer(certificate, private_key, hashes.SHA256())
		.sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
	)


def parse_login_response(response) -> Ticket:
	"""Ticket from a loginCmsResponse element. Its loginCmsReturn holds the ticket as escaped XML."""
	inner = find_text(response, "loginCmsReturn")
	if not inner:
		raise AuthError("WSAA answered without loginCmsReturn")

	ticket_xml = parse_xml(inner)
	token = find_text(ticket_xml, "token")
	sign = find_text(ticket_xml, "sign")
	expiration = find_text(ticket_xml, "expirationTime")
	if not (token and sign and expiration):
		raise AuthError("WSAA ticket is incomplete", detail=inner[:500])

	try:
		expires_at = datetime.fromisoformat(expiration)
	except ValueError as exc:
		raise AuthError("WSAA ticket has an invalid expiration time", detail=expiration) from exc
	if expires_at.tzinfo is None:
		raise AuthError("WSAA ticket expiration has no time zone", detail=expiration)

	return Ticket(token=token, sign=sign, expires_at=expires_at)


def login(
	credentials: Credentials, service: str, url: str, transport: SoapTransport, now: datetime | None = None
) -> Ticket:
	now = now or datetime.now(UTC)
	cms = base64.b64encode(sign_cms(build_login_ticket_request(service, now), credentials)).decode()
	try:
		response = transport.call(
			url, f"<wsaa:loginCms><wsaa:in0>{cms}</wsaa:in0></wsaa:loginCms>", {"wsaa": WSAA_NS}
		)
	except ServiceFault as fault:
		if fault.code.endswith("alreadyAuthenticated"):
			raise AlreadyAuthenticated(fault.message, detail=fault.code) from fault
		raise AuthError(fault.message, detail=fault.code) from fault
	return parse_login_response(response)


def get_ticket(
	store: TicketStore,
	lock: Callable[[], AbstractContextManager],
	do_login: Callable[[], Ticket],
	now: datetime | None = None,
) -> Ticket:
	"""Return the stored ticket while valid; otherwise log in once, under `lock`, and store it."""
	now = now or datetime.now(UTC)
	if (ticket := store.get()) and ticket.is_valid(now):
		return ticket

	with lock():
		# another process may have logged in while we waited for the lock
		if (ticket := store.get()) and ticket.is_valid(now):
			return ticket
		ticket = do_login()
		store.set(ticket)
		return ticket
