"""Registry of ARCA web service endpoints.

This is the only place where ARCA URLs are written down. Clients never build
URLs: they receive the endpoint already resolved by `resolve_endpoint`, so a
change announced by ARCA is a one-line edit here or an override in
ARCA Settings.
"""

from collections.abc import Mapping
from enum import StrEnum


class Environment(StrEnum):
	"""ARCA environments. Values match the Select options stored in DocTypes."""

	HOMOLOGATION = "Homologation"
	PRODUCTION = "Production"


class Service(StrEnum):
	"""ARCA services used by the app. Values match the Select options stored in DocTypes."""

	WSAA = "wsaa"
	WSMTXCA = "wsmtxca"
	PADRON_A5 = "padron_a5"
	WSCDC = "wscdc"


DEFAULT_ENDPOINTS: Mapping[tuple[Service, Environment], str] = {
	# WSAA, "Especificación técnica WSAA"
	(Service.WSAA, Environment.HOMOLOGATION): "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
	(Service.WSAA, Environment.PRODUCTION): "https://wsaa.afip.gov.ar/ws/services/LoginCms",
	# WSMTXCA, developer manual v0.25.10, p. 14
	(Service.WSMTXCA, Environment.HOMOLOGATION): "https://fwshomo.afip.gov.ar/wsmtxca/services/MTXCAService",
	(Service.WSMTXCA, Environment.PRODUCTION): (
		"https://serviciosjava.afip.gob.ar/wsmtxca/services/MTXCAService"
	),
	# Padrón A5 endpoint, used by ws_sr_constancia_inscripcion (getPersona)
	(Service.PADRON_A5, Environment.HOMOLOGATION): (
		"https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5"
	),
	(Service.PADRON_A5, Environment.PRODUCTION): (
		"https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5"
	),
	# WSCDC, "Constatación de comprobantes", manual of 2019-12-01, p. 5
	(Service.WSCDC, Environment.HOMOLOGATION): "https://wswhomo.afip.gov.ar/WSCDC/service.asmx",
	(Service.WSCDC, Environment.PRODUCTION): "https://servicios1.afip.gov.ar/WSCDC/service.asmx",
}

# Service id that WSAA expects in the LoginTicketRequest for each service.
WSAA_SERVICE_IDS: Mapping[Service, str] = {
	Service.WSMTXCA: "wsmtxca",
	Service.PADRON_A5: "ws_sr_constancia_inscripcion",
	Service.WSCDC: "wscdc",
}

# Verification URL encoded in the invoice QR ("Especificaciones del QR", ARCA).
# It is the same for every environment.
QR_BASE_URL = "https://www.arca.gob.ar/fe/qr/"


class UnknownEndpoint(LookupError):
	"""No endpoint is registered for the requested service and environment."""


def resolve_endpoint(
	service: Service | str,
	environment: Environment | str,
	overrides: Mapping[tuple[str, str], str] | None = None,
) -> str:
	"""Return the URL for `service` in `environment`.

	`overrides` maps `(service, environment)` to a URL and wins over the defaults.
	"""
	try:
		key = (Service(service), Environment(environment))
	except ValueError as exc:
		raise UnknownEndpoint(f"Unknown service or environment: {service!r}, {environment!r}") from exc

	if overrides and (url := overrides.get((key[0].value, key[1].value))):
		return url

	try:
		return DEFAULT_ENDPOINTS[key]
	except KeyError as exc:
		raise UnknownEndpoint(f"No endpoint for {key[0].value} in {key[1].value}") from exc
