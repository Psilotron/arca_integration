"""WSFE client: one ``wsfe`` ticket, then one call per operation."""

from __future__ import annotations

from datetime import datetime, timezone

from ..client import (
    TICKET_WINDOW,
    TIMEOUT_SECONDS,
    HttpPostWithAction,
    Signer,
    _default_post,
)
from ..errors import TransportError
from ..ticket_cache import MemoryTicketCache, TicketCache, is_valid
from ..wsaa import (
    Ticket,
    build_login_cms_envelope,
    build_login_ticket_request,
    parse_login_ticket_response,
    sign_cms,
)
from .cae import CaeRequest, build_cae_request, parse_cae_response
from .consulta import (
    ConsultaComprobante,
    build_consulta_request,
    parse_consulta_response,
)
from .numbering import build_last_authorized_request, parse_last_authorized
from .params import build_param_request, parse_ptos_venta, parse_tipos_cbte
from .types import Cae, ComprobanteType, PuntoVenta
from .xml import Auth, auth_xml, build_envelope, soap_action

WSFE_SERVICE = "wsfe"
WSFE_URL = "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL"


class WsfeClient:
    def __init__(
        self,
        wsaa_url: str,
        wsfe_url: str,
        cert_path: str,
        key_path: str,
        cuit_representada: str,
        cache: TicketCache | None = None,
        http_post: HttpPostWithAction | None = None,
        signer: Signer | None = None,
        timeout: int = TIMEOUT_SECONDS,
    ) -> None:
        self.wsaa_url = wsaa_url
        self.wsfe_url = wsfe_url
        self.cert_path = cert_path
        self.key_path = key_path
        self.cuit_representada = cuit_representada
        self.service = WSFE_SERVICE
        self.timeout = timeout
        self.cache = cache or MemoryTicketCache()
        self.http_post = http_post or (
            lambda url, body, action: _default_post(url, body, self.timeout, action)
        )
        self.signer = signer or (
            lambda xml: sign_cms(xml, cert_path, key_path, cuit_representada)
        )

    def ticket(self, now: datetime | None = None) -> Ticket:
        moment = now or datetime.now(timezone.utc)
        cached = self.cache.get(self.service)
        if cached is not None and is_valid(cached, moment):
            return cached

        request = build_login_ticket_request(
            service=self.service,
            unique_id=int(moment.timestamp()),
            generation_time=moment - TICKET_WINDOW,
            expiration_time=moment + TICKET_WINDOW,
        )
        envelope = build_login_cms_envelope(self.signer(request))
        answer = self._send(self.wsaa_url, envelope, "")
        ticket = parse_login_ticket_response(answer)
        self.cache.set(self.service, ticket)
        return ticket

    def call(self, operation: str, body: bytes) -> bytes:
        return self._send(self.wsfe_url, body, operation)

    def ptos_venta(self) -> list[PuntoVenta]:
        """Points of sale ARCA reports for electronic invoicing.

        Raises ``WsfeError`` when ARCA answers an error body. This is **not** a
        way to find out whether a point of sale is usable: in homologación ARCA
        answers ``602`` even when issuing from point of sale 1 works, so a raise
        here says nothing about the fiscal engine's health.
        """
        return parse_ptos_venta(self._operation("FEParamGetPtosVenta"))

    def tipos_cbte(self) -> list[ComprobanteType]:
        """Comprobante types ARCA accepts, with their validity windows."""
        return parse_tipos_cbte(self._operation("FEParamGetTiposCbte"))

    def ultimo_autorizado(self, pto_venta: int, cbte_tipo: int) -> int:
        """Last number ARCA authorized for that point of sale and type.

        ``0`` means nothing was issued there yet. It does not prove the point of
        sale exists: ARCA answers ``0`` for a nonexistent one too, and the
        catalog answers ``602`` either way. Only ``solicitar_cae`` settles it.
        """

        auth = Auth(self.ticket(), self.cuit_representada)
        request = build_last_authorized_request(auth, pto_venta, cbte_tipo)
        return parse_last_authorized(
            self.call(soap_action("FECompUltimoAutorizado"), request)
        )

    def consultar_comprobante(
        self, pto_venta: int, cbte_tipo: int, cbte_nro: int
    ) -> ConsultaComprobante:
        """Ask ARCA whether that comprobante exists, and with which CAE.

        ``existe is False`` is ARCA's ``Err 602``, its only evidence of absence.
        Any other unreadable answer raises ``WsfeError`` instead of pretending to
        be absence, because a wrong "does not exist" makes the caller issue a
        number that is already consumed.
        """
        auth = Auth(self.ticket(), self.cuit_representada)
        return parse_consulta_response(
            self.call(
                soap_action("FECompConsultar"),
                build_consulta_request(auth, pto_venta, cbte_tipo, cbte_nro),
            )
        )

    def solicitar_cae(self, request: CaeRequest) -> Cae:
        """Ask ARCA to authorize one comprobante and return its CAE.

        Raises ``WsfeRejected`` when ARCA refused it (nothing was authorized)
        and ``WsfeUncertain`` when ARCA could not settle the request (it may or
        may not exist). Neither case is retried here: an uncertain issuance has
        to be reconciled by reading the comprobante back before asking again,
        because ARCA rejects a repeated number with ``10016`` instead of
        answering with the existing CAE.
        """
        auth = Auth(self.ticket(), self.cuit_representada)
        body = build_cae_request(auth, request)
        return parse_cae_response(self.call(soap_action("FECAESolicitar"), body))

    def _operation(self, operation: str, inner: str = "") -> bytes:
        """Call ``operation`` with the ``Auth`` block already in the body."""
        auth = Auth(self.ticket(), self.cuit_representada)
        return self.call(
            soap_action(operation), build_envelope(operation, auth_xml(auth) + inner)
        )

    def _send(self, url: str, body: bytes, operation: str) -> bytes:
        try:
            return self.http_post(url, body, operation)
        except OSError as exc:
            raise TransportError("No se pudo conectar con ARCA.", detail=str(exc)) from exc
