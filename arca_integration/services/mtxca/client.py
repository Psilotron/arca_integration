"""WSMTXCA client: one method per web service operation.

Every operation takes and returns plain values or dataclasses. Business
errors (`arrayErrores`) raise `MtxcaError`; system announcements (`evento`)
are collected in `events` so the caller can show or log them.
"""

from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

from lxml import etree

from ..errors import ArcaError
from ..soap import SoapTransport, find, find_text

MTXCA_NS = "http://impl.service.wsmtxca.afip.gov.ar/service/"

# MTXCA error raised when a voucher type is not valid for the requested operation.
ERROR_INVALID_VOUCHER_TYPE = "196"


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
		response = self.transport.call(
			self.url,
			f"<ser:{operation}Request>{auth}{payload}</ser:{operation}Request>",
			{"ser": MTXCA_NS},
			soap_action=operation,
		)

		if (event := find(response, "evento")) is not None:
			self.events.append(
				CodeDescription(find_text(event, "codigo") or "", find_text(event, "descripcion") or "")
			)
		if errors := parse_code_descriptions(find(response, "arrayErrores")):
			raise MtxcaError(errors)
		return response
