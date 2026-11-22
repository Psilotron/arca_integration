"""SOAP 1.1 transport shared by every ARCA web service.

It knows nothing about vouchers or tickets: it wraps a body in an envelope,
posts it, and returns the first element inside the response Body, or raises
one of the errors in `services.errors`.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lxml import etree

from .errors import ServiceFault, TransportError, TransportTimeout

SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
DEFAULT_TIMEOUT = 30

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=True)


@dataclass(frozen=True)
class HttpResponse:
	status: int
	content: bytes


# (url, body, headers, timeout, verify) -> HttpResponse
HttpPost = Callable[[str, bytes, Mapping[str, str], int, bool | str], HttpResponse]


def requests_post(url: str, body: bytes, headers: Mapping[str, str], timeout: int, verify: bool | str):
	import requests

	try:
		response = requests.post(url, data=body, headers=dict(headers), timeout=timeout, verify=verify)
	except requests.Timeout as exc:
		raise TransportTimeout("ARCA did not answer in time", detail=f"{url}: {exc}") from exc
	except requests.RequestException as exc:
		raise TransportError("Could not connect to ARCA", detail=f"{url}: {exc}") from exc
	return HttpResponse(response.status_code, response.content)


def parse_xml(content: bytes | str) -> etree._Element:
	"""Parse XML without resolving entities or touching the network."""
	if isinstance(content, str):
		content = content.encode()
	try:
		return etree.fromstring(content, parser=_PARSER)
	except etree.XMLSyntaxError as exc:
		raise TransportError("ARCA answered with invalid XML", detail=str(exc)) from exc


def find(element: etree._Element, name: str) -> etree._Element | None:
	"""First descendant with local name `name`, ignoring namespaces."""
	found = element.xpath(".//*[local-name()=$name]", name=name)
	return found[0] if found else None


def find_text(element: etree._Element, name: str) -> str | None:
	node = find(element, name)
	return node.text.strip() if node is not None and node.text is not None else None


def build_envelope(body: str, namespaces: Mapping[str, str]) -> bytes:
	"""Wrap `body` (XML using the given prefixes) in a SOAP 1.1 envelope."""
	declarations = "".join(f' xmlns:{prefix}="{uri}"' for prefix, uri in namespaces.items())
	return (
		f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}"{declarations}>'
		f"<soapenv:Header/><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
	).encode()


class SoapTransport:
	def __init__(
		self,
		timeout: int = DEFAULT_TIMEOUT,
		verify: bool | str = True,
		post: HttpPost = requests_post,
	) -> None:
		self.timeout = timeout
		self.verify = verify
		self._post = post

	def call(
		self, url: str, body: str, namespaces: Mapping[str, str], soap_action: str = ""
	) -> etree._Element:
		"""Post `body` to `url` and return the first element inside the response Body."""
		headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{soap_action}"'}
		response = self._post(url, build_envelope(body, namespaces), headers, self.timeout, self.verify)

		try:
			root = parse_xml(response.content)
		except TransportError:
			if response.status != 200:
				raise TransportError(
					f"ARCA answered HTTP {response.status}",
					detail=response.content[:500].decode(errors="replace"),
				) from None
			raise

		if (fault := find(root, "Fault")) is not None:
			raise ServiceFault(
				code=find_text(fault, "faultcode") or "",
				message=find_text(fault, "faultstring") or "SOAP fault without message",
				detail=etree.tostring(fault, encoding="unicode")[:2000],
			)

		if response.status != 200:
			raise TransportError(f"ARCA answered HTTP {response.status}")

		body_element = root.find(f"{{{SOAP_ENV_NS}}}Body")
		if body_element is None or len(body_element) == 0:
			raise TransportError("ARCA answered without a SOAP body")
		return body_element[0]
