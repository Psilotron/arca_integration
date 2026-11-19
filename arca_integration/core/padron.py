"""Padrón constancia de inscripción client (`ws_sr_constancia_inscripcion`).

The service answers, for one CUIT, with the data printed on the taxpayer's
constancia: identity, tax status, domicile, registered taxes and activities.
Business rejections arrive as HTTP 200 with an `errorConstancia` body.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .errors import ConstanciaError, PersonaNotFound
from .wsaa import Ticket

PADRON_NAMESPACE = "http://a5.soap.ws.server.puc.sr/"

GET_PERSONA_ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:per="{ns}"><soapenv:Header/><soapenv:Body><per:getPersona>'
    "<sign>{sign}</sign><token>{token}</token>"
    "<cuitRepresentada>{cuit_representada}</cuitRepresentada>"
    "<idPersona>{id_persona}</idPersona>"
    "</per:getPersona></soapenv:Body></soapenv:Envelope>"
)

_SEPARATORS = re.compile(r"[-.\s]")


@dataclass
class Persona:
    cuit: str
    tipo_persona: str | None = None
    razon_social: str | None = None
    nombre: str | None = None
    apellido: str | None = None
    estado_clave: str | None = None
    tipo_clave: str | None = None
    domicilio: dict = field(default_factory=dict)
    impuestos: list[dict] = field(default_factory=list)
    actividades: list[dict] = field(default_factory=list)
    error_constancia: str | None = None
    raw: str = ""


def normalize_cuit(raw: str) -> str:
    cleaned = _SEPARATORS.sub("", (raw or "").strip())
    if not cleaned.isdigit() or len(cleaned) != 11:
        raise ValueError(f"CUIT inválido: {raw!r}")
    return cleaned


def build_get_persona_envelope(ticket: Ticket, cuit_representada: str, id_persona: str) -> bytes:
    xml = GET_PERSONA_ENVELOPE.format(
        ns=PADRON_NAMESPACE,
        sign=ticket.sign,
        token=ticket.token,
        cuit_representada=cuit_representada,
        id_persona=id_persona,
    )
    return xml.encode("utf-8")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node: ET.Element, name: str) -> ET.Element | None:
    for element in node.iter():
        if _local(element.tag) == name:
            return element
    return None


def _as_dict(node: ET.Element) -> dict:
    return {_local(child.tag): (child.text or "").strip() for child in node}


def _as_list(node: ET.Element | None, item_name: str) -> list[dict]:
    if node is None:
        return []
    return [_as_dict(child) for child in node if _local(child.tag) == item_name]


def _value(mapping: dict, key: str) -> str | None:
    value = mapping.get(key)
    return value or None


def parse_persona_response(body: bytes) -> Persona:
    text = body.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ConstanciaError("ARCA devolvió una respuesta ilegible.", detail=str(exc)) from exc

    fault = _find(root, "faultstring")
    if fault is not None:
        raise PersonaNotFound(
            "ARCA no encontró una persona con ese CUIT.",
            detail=(fault.text or "").strip(),
        )

    persona_return = _find(root, "personaReturn")
    if persona_return is None:
        raise ConstanciaError("ARCA devolvió una respuesta inesperada.", detail=text[:500])

    generales_node = _find(persona_return, "datosGenerales")
    error_node = _find(persona_return, "errorConstancia")
    generales = _as_dict(generales_node) if generales_node is not None else {}
    error = _as_dict(error_node) if error_node is not None else {}

    cuit = _value(generales, "idPersona") or _value(error, "idPersona") or ""
    domicilio_node = _find(persona_return, "domicilio")

    return Persona(
        cuit=cuit,
        tipo_persona=_value(generales, "tipoPersona"),
        razon_social=_value(generales, "razonSocial"),
        nombre=_value(generales, "nombre"),
        apellido=_value(generales, "apellido"),
        estado_clave=_value(generales, "estadoClave"),
        tipo_clave=_value(generales, "tipoClave"),
        domicilio=_as_dict(domicilio_node) if domicilio_node is not None else {},
        impuestos=_as_list(_find(persona_return, "impuestos"), "impuesto"),
        actividades=_as_list(_find(persona_return, "actividades"), "actividad"),
        error_constancia=_value(error, "error"),
        raw=text,
    )
