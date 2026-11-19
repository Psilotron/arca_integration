"""SOAP envelope construction and namespace-blind XML reading for WSFE.

Two rules make this module small and safe:

``ar:`` prefix on every element. ARCA's SOAP stack silently zeroes unqualified
children, so a request without the prefix answers with defaults instead of an
error. Every element this module writes is namespaced.

Read by local name. ARCA answers with ``<soap:Body>`` and an unqualified body, so
parsers match on the tag's local name and never on a prefix.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..wsaa import Ticket

NAMESPACE = "http://ar.gov.afip.dif.FEV1/"
SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"

_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'


@dataclass(frozen=True)
class Auth:
    """The ``Auth`` block WSFE requires on every authenticated operation."""

    ticket: Ticket
    cuit: str


def auth_xml(auth: Auth) -> str:
    return (
        "<ar:Auth>"
        f"<ar:Token>{auth.ticket.token}</ar:Token>"
        f"<ar:Sign>{auth.ticket.sign}</ar:Sign>"
        f"<ar:Cuit>{auth.cuit}</ar:Cuit>"
        "</ar:Auth>"
    )


def build_envelope(operation: str, inner_xml: str) -> bytes:
    """Wrap ``inner_xml`` in the operation's SOAP body.

    ``Auth`` must already be the first element of ``inner_xml``: SOAP 1.1
    document/literal is order sensitive.
    """
    return (
        _XML_DECLARATION
        + f'<soapenv:Envelope xmlns:soapenv="{SOAP_NAMESPACE}" '
        + f'xmlns:ar="{NAMESPACE}">'
        + "<soapenv:Header/>"
        + f"<soapenv:Body><ar:{operation}>{inner_xml}</ar:{operation}></soapenv:Body>"
        + "</soapenv:Envelope>"
    ).encode()


def soap_action(operation: str) -> str:
    """The fully qualified SOAPAction for ``operation``.

    A bare operation name is accepted by the transport but produces a 200 with a
    useless body, so callers never build the action themselves.
    """
    return f"{NAMESPACE}{operation}"


def parse_root(body: bytes) -> ET.Element:
    return ET.fromstring(body)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find(node: ET.Element, name: str) -> ET.Element | None:
    for child in node.iter():
        if local_name(child.tag) == name:
            return child
    return None


def find_all(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node.iter() if local_name(child.tag) == name]


def text_of(node: ET.Element | None, name: str) -> str | None:
    if node is None:
        return None
    child = find(node, name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def first_error(root: ET.Element) -> tuple[int | None, str] | None:
    """Return ``(code, message)`` for the first ``Errors.Err``, else ``None``."""
    errors = find(root, "Errors")
    if errors is None:
        return None
    for err in find_all(errors, "Err"):
        message = text_of(err, "Msg") or "ARCA no indicó el motivo."
        raw_code = text_of(err, "Code")
        code: int | None
        try:
            code = int(raw_code) if raw_code is not None else None
        except ValueError:
            code = None
        return code, message
    return None


def as_number(value: str | None) -> int | None:
    """Read an integer field, tolerating ARCA's ``NULL`` and empty values."""
    if value is None or value.upper() == "NULL":
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group(0)) if match else None
