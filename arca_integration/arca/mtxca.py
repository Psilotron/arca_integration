"""Frappe adapter that builds authenticated ARCA clients (WSMTXCA, WSCDC) for a company."""

from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import (
	get_access_ticket,
	obtain_ticket,
)
from arca_integration.arca.doctype.arca_company_settings.arca_company_settings import get_company_settings
from arca_integration.arca.doctype.arca_settings.arca_settings import get_endpoint, get_transport
from arca_integration.services.endpoints import Service
from arca_integration.services.mtxca.client import Auth, MtxcaClient
from arca_integration.services.wsaa import Ticket
from arca_integration.services.wscdc.client import WscdcClient


def get_mtxca_client(company: str) -> MtxcaClient:
	"""Client for the company's active environment. May log in to WSAA (and commit)."""
	return mtxca_client_for(company, get_access_ticket(company, Service.WSMTXCA))


def get_status_client(company: str) -> MtxcaClient:
	"""Unauthenticated client, enough for `dummy`."""
	settings = get_company_settings(company)
	return MtxcaClient(url=get_endpoint(Service.WSMTXCA, settings.environment), transport=get_transport())


def mtxca_client_for(company: str, ticket: Ticket) -> MtxcaClient:
	settings = get_company_settings(company)
	return MtxcaClient(
		url=get_endpoint(Service.WSMTXCA, settings.environment),
		transport=get_transport(),
		auth=Auth(token=ticket.token, sign=ticket.sign, cuit=settings.cuit),
	)


def get_wscdc_client(company: str, ticket: Ticket | None = None) -> WscdcClient:
	"""Client of WSCDC (Constatación de Comprobantes). Without `ticket`, may log in (and commit)."""
	settings = get_company_settings(company)
	ticket = ticket or obtain_ticket(company, Service.WSCDC)
	return WscdcClient(
		url=get_endpoint(Service.WSCDC, settings.environment),
		transport=get_transport(),
		auth=Auth(token=ticket.token, sign=ticket.sign, cuit=settings.cuit),
	)
