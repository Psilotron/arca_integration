"""ARCA lookup orchestration: authenticate once, then query the padrón."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .errors import ConstanciaError, TransportError
from .padron import Persona, build_get_persona_envelope, normalize_cuit, parse_persona_response
from .ticket_cache import MemoryTicketCache, TicketCache, is_valid
from .wsaa import (
    Ticket,
    build_login_cms_envelope,
    build_login_ticket_request,
    parse_login_ticket_response,
    sign_cms,
)

HttpPost = Callable[[str, bytes], bytes]
HttpPostWithAction = Callable[[str, bytes, str], bytes]
Signer = Callable[[str], bytes]

SERVICE = "ws_sr_constancia_inscripcion"
TICKET_WINDOW = timedelta(minutes=10)
TIMEOUT_SECONDS = 30


def _ca_bundle() -> str:
    """CA bundle for ARCA's endpoints, or ``""`` to keep the default.

    Verification stays at whatever ``requests`` uses by default (the ``certifi``
    bundle of the interpreter running bench) unless a site points this app at
    its own bundle with ``ARCA_CA_BUNDLE``. Some ARCA endpoints do not send
    their intermediate certificate, and only this app should ever see the
    bundle that fixes it: the rest of the bench keeps its own trust settings.
    """
    ruta = os.environ.get("ARCA_CA_BUNDLE", "").strip()

    if not ruta:
        return ""

    if not Path(ruta).is_file():
        raise TransportError(
            "ARCA_CA_BUNDLE no apunta a un archivo de certificados.",
            detail=ruta,
        )

    return ruta


def _default_post(
    url: str, body: bytes, timeout: int = TIMEOUT_SECONDS, soap_action: str = ""
) -> bytes:
    import requests

    verify = _ca_bundle()

    try:
        response = requests.post(
            url,
            data=body,
            headers={
                "Content-Type": "text/xml;charset=UTF-8",
                "SOAPAction": f'"{soap_action}"',
            },
            timeout=timeout,
            **({"verify": verify} if verify else {}),
        )
    except requests.RequestException as exc:
        raise TransportError("No se pudo conectar con ARCA.", detail=str(exc)) from exc

    if response.status_code != 200:
        text = response.text or ""
        # ARCA answers SOAP faults with HTTP 500; the fault body carries the reason.
        if "<faultstring>" not in text:
            raise TransportError(
                f"ARCA respondió HTTP {response.status_code}.",
                detail=text[:500],
            )
    return response.content


class ArcaClient:
    def __init__(
        self,
        wsaa_url: str,
        padron_url: str,
        cert_path: str,
        key_path: str,
        cuit_representada: str,
        service: str = SERVICE,
        cache: TicketCache | None = None,
        http_post: HttpPost | None = None,
        signer: Signer | None = None,
        timeout: int = TIMEOUT_SECONDS,
    ) -> None:
        self.wsaa_url = wsaa_url
        self.padron_url = padron_url
        self.cert_path = cert_path
        self.key_path = key_path
        self.cuit_representada = cuit_representada
        self.service = service
        self.cache = cache or MemoryTicketCache()
        self.http_post = http_post or (
            lambda url, body: _default_post(url, body, self.timeout)
        )
        self.signer = signer or (
            lambda xml: sign_cms(xml, cert_path, key_path, cuit_representada)
        )
        self.timeout = timeout

    def lookup(self, cuit: str, now: datetime | None = None) -> Persona:
        cuit = normalize_cuit(cuit)
        moment = now or datetime.now(timezone.utc)
        ticket = self._ticket(moment)

        body = build_get_persona_envelope(ticket, self.cuit_representada, cuit)
        answer = self._call(self.padron_url, body)
        persona = parse_persona_response(answer)

        if persona.error_constancia:
            raise ConstanciaError(
                persona.error_constancia,
                detail=f"CUIT {cuit}",
            )

        persona.cuit = persona.cuit or cuit
        return persona

    def _call(self, url: str, body: bytes) -> bytes:
        try:
            return self.http_post(url, body)
        except OSError as exc:
            raise TransportError("No se pudo conectar con ARCA.", detail=str(exc)) from exc

    def _ticket(self, now: datetime) -> Ticket:
        cached = self.cache.get(self.service)
        if cached is not None and is_valid(cached, now):
            return cached

        request = build_login_ticket_request(
            service=self.service,
            unique_id=int(now.timestamp()),
            generation_time=now - TICKET_WINDOW,
            expiration_time=now + TICKET_WINDOW,
        )
        envelope = build_login_cms_envelope(self.signer(request))
        answer = self._call(self.wsaa_url, envelope)
        ticket = parse_login_ticket_response(answer)
        self.cache.set(self.service, ticket)
        return ticket
