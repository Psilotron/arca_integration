"""Frappe adapter that builds an authenticated MTXCA client for a company."""

from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import get_access_ticket
from arca_integration.arca.doctype.arca_company_settings.arca_company_settings import get_company_settings
from arca_integration.arca.doctype.arca_settings.arca_settings import get_endpoint, get_transport
from arca_integration.services.endpoints import Service
from arca_integration.services.mtxca.client import Auth, MtxcaClient


def get_mtxca_client(company: str) -> MtxcaClient:
	"""Client for the company's active environment. May log in to WSAA (and commit)."""
	settings = get_company_settings(company)
	ticket = get_access_ticket(company, Service.WSMTXCA)
	return MtxcaClient(
		url=get_endpoint(Service.WSMTXCA, settings.environment),
		transport=get_transport(),
		auth=Auth(token=ticket.token, sign=ticket.sign, cuit=settings.cuit),
	)
