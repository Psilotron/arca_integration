# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

from datetime import UTC
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import convert_utc_to_system_timezone, get_datetime, get_system_timezone
from frappe.utils.synchronization import filelock

from arca_integration.arca.doctype.arca_company_settings.arca_company_settings import get_company_settings
from arca_integration.arca.doctype.arca_settings.arca_settings import get_endpoint, get_transport
from arca_integration.services import wsaa
from arca_integration.services.endpoints import WSAA_SERVICE_IDS, Environment, Service
from arca_integration.services.errors import AlreadyAuthenticated, AuthError, TransportError
from arca_integration.services.wsaa import Ticket

DOCTYPE = "ARCA Access Ticket"


class ARCAAccessTicket(Document):
	def autoname(self):
		self.name = ticket_name(self.company, self.environment, self.service)


def ticket_name(company: str, environment: str, service: str) -> str:
	return f"{company}-{environment}-{service}"


class DocTicketStore:
	"""Keeps one WSAA ticket per company, environment and service in ARCA Access Ticket."""

	def __init__(self, company: str, environment: str, service: str) -> None:
		self.company, self.environment, self.service = company, environment, service
		self.name = ticket_name(company, environment, service)

	def get(self) -> Ticket | None:
		if not frappe.db.exists(DOCTYPE, self.name):
			return None
		doc = frappe.get_doc(DOCTYPE, self.name)
		expires_at = get_datetime(doc.expires_at).replace(tzinfo=ZoneInfo(get_system_timezone()))
		return Ticket(token=doc.get_password("token"), sign=doc.get_password("sign"), expires_at=expires_at)

	def set(self, ticket: Ticket) -> None:
		if frappe.db.exists(DOCTYPE, self.name):
			doc = frappe.get_doc(DOCTYPE, self.name)
		else:
			doc = frappe.new_doc(DOCTYPE)
			doc.update({"company": self.company, "environment": self.environment, "service": self.service})

		doc.token = ticket.token
		doc.sign = ticket.sign
		doc.expires_at = convert_utc_to_system_timezone(ticket.expires_at.astimezone(UTC)).replace(
			tzinfo=None
		)
		doc.save(ignore_permissions=True)
		# Commit now: if the caller's transaction rolls back, a lost ticket blocks
		# new logins for up to 12 hours (coe.alreadyAuthenticated).
		frappe.db.commit()  # nosemgrep


def get_access_ticket(company: str, service: Service | str) -> Ticket:
	"""Valid WSAA ticket for `company` and `service` in the company's active environment.

	May log in to WSAA and commits the current transaction when it does, so call
	it before changing documents.
	"""
	service = Service(service)
	settings = get_company_settings(company)
	environment = Environment(settings.environment)
	credentials = settings.get_credentials(environment)
	service_id = WSAA_SERVICE_IDS[service]
	store = DocTicketStore(company, environment.value, service_id)

	def do_login() -> Ticket:
		return wsaa.login(credentials, service_id, get_endpoint(Service.WSAA, environment), get_transport())

	try:
		return wsaa.get_ticket(
			store,
			lock=lambda: filelock(f"arca-wsaa-{frappe.scrub(store.name)}", timeout=60),
			do_login=do_login,
		)
	except AlreadyAuthenticated:
		frappe.throw(
			_(
				"ARCA already issued an access ticket for this certificate and service, but this site did not keep it. Try again when it expires (up to 12 hours) or use a new certificate."
			),
			title=_("ARCA Login"),
		)
	except AuthError as exc:
		frappe.throw(_("ARCA rejected the login: {0}").format(exc.message), title=_("ARCA Login"))
	except TransportError as exc:
		frappe.throw(_("Could not connect to ARCA: {0}").format(exc.message), title=_("ARCA Login"))
