"""WSMTXCA client: one method per web service operation.

Every operation takes and returns plain values or dataclasses. Business
errors (`arrayErrores`) raise `MtxcaError`; system announcements (`evento`)
are collected in `events` so the caller can show or log them.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from lxml import etree

from ..errors import ArcaError
from ..soap import SoapTransport, find, find_text
from .requests import voucher_xml
from .voucher import Voucher

MTXCA_NS = "http://impl.service.wsmtxca.afip.gov.ar/service/"

# MTXCA error raised when a voucher type is not valid for the requested operation.
ERROR_INVALID_VOUCHER_TYPE = "196"
# consultarComprobante: ARCA has no voucher with that type, point of sale and number.
ERROR_VOUCHER_NOT_FOUND = "1503"

_SECRETS_RE = re.compile(r"<(token|sign)>.*?</\1>", re.S)


@dataclass(frozen=True)
class Auth:
	token: str
	sign: str
	cuit: str


@dataclass(frozen=True)
class CodeDescription:
	code: str
	description: str


@dataclass(frozen=True)
class PointOfSale:
	number: int
	blocked: bool
	deactivated_on: date | None


@dataclass(frozen=True)
class ServerStatus:
	app_server: str
	db_server: str
	auth_server: str


@dataclass(frozen=True)
class Authorization:
	"""Answer of autorizarComprobante when ARCA authorized the voucher (resultado A or O)."""

	result: str  # A (authorized) or O (authorized with observations)
	cae: str
	cae_due_date: date
	number: int
	issue_date: date
	observations: tuple[CodeDescription, ...] = ()


@dataclass(frozen=True)
class AuthorizedVoucher:
	"""A voucher as ARCA keeps it (consultarComprobante)."""

	voucher_type: int
	point_of_sale: int
	number: int
	issue_date: date | None
	cae: str
	cae_due_date: date | None
	total: Decimal
	document_type: str
	document_number: str


@dataclass(frozen=True)
class Exchange:
	"""One request and its answer, without the WSAA token and sign, for the request log."""

	operation: str
	request: str
	response: str = ""


class MtxcaError(ArcaError):
	"""MTXCA rejected the request with `arrayErrores`."""

	def __init__(self, errors: list[CodeDescription]) -> None:
		message = "; ".join(f"{e.code}: {e.description}" for e in errors) or "MTXCA error"
		super().__init__(message)
		self.errors = errors

	@property
	def codes(self) -> set[str]:
		return {e.code for e in self.errors}


def parse_code_descriptions(element: etree._Element | None) -> list[CodeDescription]:
	"""Children `codigoDescripcion` of an Array*Type element."""
	if element is None:
		return []
	return [
		CodeDescription(
			code=find_text(item, "codigo") or "", description=find_text(item, "descripcion") or ""
		)
		for item in element.xpath("./*[local-name()='codigoDescripcion']")
	]


@dataclass
class MtxcaClient:
	url: str
	transport: SoapTransport
	auth: Auth | None = None
	events: list[CodeDescription] = field(default_factory=list)
	exchanges: list[Exchange] = field(default_factory=list)

	# Operations without parameters that return an ArrayCodigosDescripcionesType.
	def voucher_types(self) -> list[CodeDescription]:
		return self._catalog("consultarTiposComprobante", "arrayTiposComprobante")

	def document_types(self) -> list[CodeDescription]:
		return self._catalog("consultarTiposDocumento", "arrayTiposDocumento")

	def vat_conditions(self) -> list[CodeDescription]:
		return self._catalog("consultarCondicionesIVA", "arrayCondicionesIVA")

	def currencies(self) -> list[CodeDescription]:
		return self._catalog("consultarMonedas", "arrayMonedas")

	def units_of_measure(self) -> list[CodeDescription]:
		return self._catalog("consultarUnidadesMedida", "arrayUnidadesMedida")

	def tax_types(self) -> list[CodeDescription]:
		return self._catalog("consultarTiposTributo", "arrayTiposTributo")

	def receiver_vat_conditions(self, voucher_type: int | str) -> list[CodeDescription]:
		"""Receiver VAT conditions allowed for `voucher_type` (RG 5616)."""
		response = self._call(
			"consultarCondicionesIVAReceptor",
			"<consultaCondicionesIVAReceptorRequest>"
			f"<codigoTipoComprobante>{int(voucher_type)}</codigoTipoComprobante>"
			"</consultaCondicionesIVAReceptorRequest>",
		)
		return parse_code_descriptions(find(response, "arrayCondicionesIVAReceptor"))

	def points_of_sale_cae(self) -> list[PointOfSale]:
		response = self._call("consultarPuntosVentaCAE")
		result = []
		for item in response.xpath(".//*[local-name()='puntoVenta']"):
			deactivated = find_text(item, "fechaBaja")
			result.append(
				PointOfSale(
					number=int(find_text(item, "numeroPuntoVenta")),
					# the schema says S/N; the manual's example answers Si/No
					blocked=(find_text(item, "bloqueado") or "N").upper().startswith("S"),
					deactivated_on=date.fromisoformat(deactivated[:10]) if deactivated else None,
				)
			)
		return result

	def last_authorized(self, voucher_type: int, point_of_sale: int) -> int:
		"""Number of the last voucher ARCA authorized for this type and point of sale; 0 if none."""
		response = self._call(
			"consultarUltimoComprobanteAutorizado",
			"<consultaUltimoComprobanteAutorizadoRequest>"
			f"<codigoTipoComprobante>{int(voucher_type)}</codigoTipoComprobante>"
			f"<numeroPuntoVenta>{int(point_of_sale)}</numeroPuntoVenta>"
			"</consultaUltimoComprobanteAutorizadoRequest>",
		)
		return int(find_text(response, "numeroComprobante") or 0)

	def authorize(self, voucher: Voucher) -> Authorization:
		"""Request the CAE. Rejections (resultado R) raise `MtxcaError`."""
		response = self._call("autorizarComprobante", voucher_xml(voucher))
		result = find_text(response, "resultado") or ""
		authorized = find(response, "comprobanteResponse")
		if result not in ("A", "O") or authorized is None:
			raise MtxcaError([CodeDescription("", f"ARCA answered resultado {result or '-'} without a CAE")])
		return Authorization(
			result=result,
			cae=find_text(authorized, "CAE") or "",
			cae_due_date=date.fromisoformat(find_text(authorized, "fechaVencimientoCAE")[:10]),
			number=int(find_text(authorized, "numeroComprobante")),
			issue_date=date.fromisoformat(find_text(authorized, "fechaEmision")[:10]),
			observations=tuple(parse_code_descriptions(find(response, "arrayObservaciones"))),
		)

	def get_voucher(self, voucher_type: int, point_of_sale: int, number: int) -> AuthorizedVoucher | None:
		"""The voucher ARCA authorized with this type, point of sale and number, or None."""
		try:
			response = self._call(
				"consultarComprobante",
				"<consultaComprobanteRequest>"
				f"<codigoTipoComprobante>{int(voucher_type)}</codigoTipoComprobante>"
				f"<numeroPuntoVenta>{int(point_of_sale)}</numeroPuntoVenta>"
				f"<numeroComprobante>{int(number)}</numeroComprobante>"
				"</consultaComprobanteRequest>",
			)
		except MtxcaError as exc:
			if ERROR_VOUCHER_NOT_FOUND in exc.codes:
				return None
			raise
		voucher = find(response, "comprobante")
		if voucher is None:
			return None

		def day(name):
			text = find_text(voucher, name)
			return date.fromisoformat(text[:10]) if text else None

		return AuthorizedVoucher(
			voucher_type=int(find_text(voucher, "codigoTipoComprobante")),
			point_of_sale=int(find_text(voucher, "numeroPuntoVenta")),
			number=int(find_text(voucher, "numeroComprobante")),
			issue_date=day("fechaEmision"),
			cae=find_text(voucher, "codigoAutorizacion") or "",
			cae_due_date=day("fechaVencimiento"),
			total=Decimal(find_text(voucher, "importeTotal") or "0"),
			document_type=find_text(voucher, "codigoTipoDocumento") or "",
			document_number=find_text(voucher, "numeroDocumento") or "",
		)

	def dummy(self) -> ServerStatus:
		"""Health check of ARCA's servers. Needs no authentication."""
		response = self.transport.call(self.url, "", {"ser": MTXCA_NS}, soap_action="dummy")
		return ServerStatus(
			app_server=find_text(response, "appserver") or "",
			db_server=find_text(response, "dbserver") or "",
			auth_server=find_text(response, "authserver") or "",
		)

	def _catalog(self, operation: str, array: str) -> list[CodeDescription]:
		return parse_code_descriptions(find(self._call(operation), array))

	def _call(self, operation: str, payload: str = "") -> etree._Element:
		if self.auth is None:
			raise ArcaError(f"{operation} needs authentication")

		auth = (
			"<authRequest>"
			f"<token>{escape(self.auth.token)}</token>"
			f"<sign>{escape(self.auth.sign)}</sign>"
			f"<cuitRepresentada>{int(self.auth.cuit)}</cuitRepresentada>"
			"</authRequest>"
		)
		body = f"<ser:{operation}Request>{auth}{payload}</ser:{operation}Request>"
		exchange = Exchange(operation, _SECRETS_RE.sub(r"<\1>***</\1>", body))
		self.exchanges.append(exchange)
		response = self.transport.call(self.url, body, {"ser": MTXCA_NS}, soap_action=operation)
		self.exchanges[-1] = Exchange(
			operation, exchange.request, etree.tostring(response, encoding="unicode", pretty_print=True)
		)

		if (event := find(response, "evento")) is not None:
			self.events.append(
				CodeDescription(find_text(event, "codigo") or "", find_text(event, "descripcion") or "")
			)
		if errors := parse_code_descriptions(find(response, "arrayErrores")):
			raise MtxcaError(errors)
		return response
