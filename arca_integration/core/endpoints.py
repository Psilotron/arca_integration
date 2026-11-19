"""Canonical ARCA service endpoints by environment."""

ENDPOINTS = {
    "Homologación": {
        "wsaa_url": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        "padron_url": (
            "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5"
        ),
        "wsfe_url": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
    "Producción": {
        "wsaa_url": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
        "padron_url": (
            "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5"
        ),
        "wsfe_url": "https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
}


class UnknownArcaEnvironment(ValueError):
    """Raised when an ARCA environment has no configured service endpoints."""


def endpoints_para_ambiente(environment: str) -> dict[str, str]:
    try:
        return dict(ENDPOINTS[environment])
    except KeyError as exc:
        raise UnknownArcaEnvironment(
            "Elegí el ambiente de ARCA: Homologación o Producción."
        ) from exc
