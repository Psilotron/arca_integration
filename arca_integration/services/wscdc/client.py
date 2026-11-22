"""WSCDC (Constatación de Comprobantes): does ARCA know a received voucher?

Manual for the developer, revision of 2019-12-01. The client sends the
voucher's data and ARCA answers A (constatado) or R, with the reason in the
observations. ARCA returns no data of its own: everything must be sent.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from lxml import etree

from ..errors import ArcaError
from ..mtxca.client import Auth, CodeDescription, Exchange
from ..soap import SoapTransport, find, find_text

WSCDC_NS = "http://servicios1.afip.gob.ar/wscdc/"
CUIT_DOCUMENT = "80"
MODES = ("CAE", "CAEA", "CAI")

_SECRETS_RE = re.compile(r"<(Token|Sign)>.*?</\1>", re.S)


@dataclass(frozen=True)
class ReceivedVoucher:
	"""CmpReq: the voucher to verify, as printed on it."""

	mode: str  # CbteModo: CAE, CAEA or CAI
	issuer_cuit: str  # CuitEmisor
	point_of_sale: int  # PtoVta
	voucher_type: int  # CbteTipo
	number: int  # CbteNro
	issue_date: date  # CbteFch
	total: Decimal  # ImpTotal
	authorization_code: str  # CodAutorizacion
	receiver_document_type: str = ""  # DocTipoReceptor
	receiver_document_number: str = ""  # DocNroReceptor


@dataclass(frozen=True)
class Verification:
	approved: bool  # Resultado A
	observations: tuple[CodeDescription, ...] = ()
	processed_at: str = ""  # FchProceso


class WscdcError(ArcaError):
	"""ARCA rejected the request itself (Errors): format or authentication."""

	def __init__(self, errors: list[CodeDescription]) -> None:
		self.errors = errors
		super().__init__("; ".join(f"{e.code}: {e.description}" for e in errors))

	@property
	def codes(self) -> set[str]:
		return {e.code for e in self.errors}


def _is_service_error(code: str) -> bool:
	"""500-503: ARCA's infrastructure; 600-602: authentication. The rest are about the voucher's data."""
	return not code.isdigit() or int(code) >= 500


def _codes(element: etree._Element | None, item: str) -> list[CodeDescription]:
	if element is None:
		return []
	return [
		CodeDescription(find_text(node, "Code") or "", find_text(node, "Msg") or "")
		for node in element.xpath("./*[local-name()=$item]", item=item)
	]


def request_xml(voucher: ReceivedVoucher) -> str:
	parts = [
		("CbteModo", voucher.mode),
		("CuitEmisor", int(voucher.issuer_cuit)),
		("PtoVta", int(voucher.point_of_sale)),
		("CbteTipo", int(voucher.voucher_type)),
		("CbteNro", int(voucher.number)),
		("CbteFch", voucher.issue_date.strftime("%Y%m%d")),
		("ImpTotal", f"{abs(voucher.total):.2f}"),
		("CodAutorizacion", voucher.authorization_code),
		("DocTipoReceptor", voucher.receiver_document_type),
		("DocNroReceptor", voucher.receiver_document_number),
	]
	return (
		"<CmpReq>"
		+ "".join(f"<{n}>{escape(str(v))}</{n}>" for n, v in parts if v not in ("", None))
		+ "</CmpReq>"
	)


@dataclass
class WscdcClient:
	url: str
	transport: SoapTransport
	auth: Auth | None = None
	events: list[CodeDescription] = field(default_factory=list)
	exchanges: list[Exchange] = field(default_factory=list)

	def verify(self, voucher: ReceivedVoucher) -> Verification:
		"""ComprobanteConstatar. Rejections by data (R with observations) are a Verification, not an error."""
		try:
			result = self._call("ComprobanteConstatar", request_xml(voucher))
		except WscdcError as exc:
			if any(_is_service_error(code) for code in exc.codes):
				raise
			return Verification(approved=False, observations=tuple(exc.errors))  # wrong data format
		return Verification(
			approved=(find_text(result, "Resultado") or "") == "A",
			observations=tuple(_codes(find(result, "Observaciones"), "Obs")),
			processed_at=find_text(result, "FchProceso") or "",
		)

	def voucher_types(self) -> list[CodeDescription]:
		"""ComprobantesTipoConsultar: every voucher type that can be verified."""
		result = self._call("ComprobantesTipoConsultar")
		return [
			CodeDescription(find_text(node, "Id") or "", find_text(node, "Desc") or "")
			for node in result.xpath(".//*[local-name()='CbteTipo']")
		]

	def _call(self, operation: str, payload: str = "") -> etree._Element:
		if self.auth is None:
			raise ArcaError(f"{operation} needs authentication")
		auth = (
			"<Auth>"
			f"<Token>{escape(self.auth.token)}</Token>"
			f"<Sign>{escape(self.auth.sign)}</Sign>"
			f"<Cuit>{int(self.auth.cuit)}</Cuit>"
			"</Auth>"
		)
		body = f'<{operation} xmlns="{WSCDC_NS}">{auth}{payload}</{operation}>'
		exchange = Exchange(operation, _SECRETS_RE.sub(r"<\1>***</\1>", body))
		self.exchanges.append(exchange)
		response = self.transport.call(self.url, body, {}, soap_action=WSCDC_NS + operation)
		self.exchanges[-1] = Exchange(
			operation, exchange.request, etree.tostring(response, encoding="unicode", pretty_print=True)
		)
		result = find(response, f"{operation}Result")
		if result is None:
			raise ArcaError(f"{operation}: ARCA answered without a result")
		self.events += _codes(find(result, "Events"), "Evt")
		if errors := _codes(find(result, "Errors"), "Err"):
			raise WscdcError(errors)
		return result
