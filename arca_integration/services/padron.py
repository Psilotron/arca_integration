"""Taxpayer lookup in ARCA's registry (ws_sr_constancia_inscripcion, getPersona).

The answer carries the data printed on the taxpayer's constancia de
inscripción. It does not state the VAT condition directly: `suggested_vat_condition`
derives it from the registered taxes, as a suggestion the user confirms.
"""

from dataclasses import dataclass
from xml.sax.saxutils import escape

from lxml import etree

from .credentials import normalize_cuit
from .errors import ArcaError, ServiceFault
from .mtxca.client import Auth
from .soap import SoapTransport, find, find_text

PADRON_NS = "http://a5.soap.ws.server.puc.sr/"

# Receiver VAT condition codes (consultarCondicionesIVAReceptor).
VAT_REGISTERED = "1"
VAT_EXEMPT = "4"
FINAL_CONSUMER = "5"
MONOTRIBUTO = "6"

# Tax ids in the padrón.
TAX_VAT = "30"
TAX_VAT_EXEMPT = "32"

DOCUMENT_TYPES = {"CUIT": "80", "CUIL": "86", "CDI": "87"}


class TaxpayerNotFound(ArcaError):
	"""ARCA has no taxpayer with that CUIT."""


@dataclass(frozen=True)
class Address:
	street: str = ""
	number: str = ""
	floor: str = ""
	apartment: str = ""
	postal_code: str = ""
	city: str = ""
	province: str = ""
	kind: str = ""

	@property
	def line1(self) -> str:
		parts = [f"{self.street} {self.number}".strip()]
		if self.floor:
			parts.append(f"Piso {self.floor}")
		if self.apartment:
			parts.append(f"Dto. {self.apartment}")
		return ", ".join(p for p in parts if p)


@dataclass(frozen=True)
class Tax:
	id: str
	description: str
	status: str


@dataclass(frozen=True)
class Taxpayer:
	cuit: str
	person_type: str = ""  # FISICA / JURIDICA
	key_type: str = ""  # CUIT / CUIL / CDI
	key_status: str = ""  # ACTIVO / INACTIVO
	business_name: str = ""
	first_name: str = ""
	last_name: str = ""
	address: Address | None = None
	taxes: tuple[Tax, ...] = ()
	activities: tuple[str, ...] = ()
	constancia_error: str = ""

	@property
	def name(self) -> str:
		return self.business_name or " ".join(p for p in (self.last_name, self.first_name) if p)

	@property
	def is_company(self) -> bool:
		return self.person_type == "JURIDICA"

	@property
	def is_active(self) -> bool:
		return self.key_status == "ACTIVO"

	@property
	def document_type(self) -> str:
		return DOCUMENT_TYPES.get(self.key_type, DOCUMENT_TYPES["CUIT"])

	def has_tax(self, tax_id: str) -> bool:
		return any(t.id == tax_id and t.status == "ACTIVO" for t in self.taxes)

	@property
	def suggested_vat_condition(self) -> str:
		if any("MONOTRIBUTO" in t.description.upper() and t.status == "ACTIVO" for t in self.taxes):
			return MONOTRIBUTO
		if self.has_tax(TAX_VAT):
			return VAT_REGISTERED
		if self.has_tax(TAX_VAT_EXEMPT):
			return VAT_EXEMPT
		return FINAL_CONSUMER


def _text(element, name: str) -> str:
	return find_text(element, name) or "" if element is not None else ""


def parse_taxpayer(response: etree._Element) -> Taxpayer:
	persona = find(response, "personaReturn")
	if persona is None:
		raise ArcaError("Unexpected padrón answer without personaReturn")

	general = find(persona, "datosGenerales")
	error = find(persona, "errorConstancia")
	source = general if general is not None else error
	address = find(persona, "domicilio")

	return Taxpayer(
		cuit=_text(source, "idPersona"),
		person_type=_text(general, "tipoPersona"),
		key_type=_text(general, "tipoClave"),
		key_status=_text(general, "estadoClave"),
		business_name=_text(general, "razonSocial"),
		first_name=_text(source, "nombre"),
		last_name=_text(source, "apellido"),
		address=Address(
			street=_text(address, "calle"),
			number=_text(address, "numero"),
			floor=_text(address, "piso"),
			apartment=_text(address, "oficinaDptoLocal"),
			postal_code=_text(address, "codPostal"),
			city=_text(address, "localidad") or _text(address, "descripcionProvincia"),
			province=_text(address, "descripcionProvincia"),
			kind=_text(address, "tipoDomicilio"),
		)
		if address is not None
		else None,
		taxes=tuple(
			Tax(_text(t, "idImpuesto"), _text(t, "descripcionImpuesto"), _text(t, "estado"))
			for t in persona.xpath(".//*[local-name()='impuesto']")
		),
		activities=tuple(
			_text(a, "descripcionActividad") for a in persona.xpath(".//*[local-name()='actividad']")
		),
		constancia_error=_text(error, "error"),
	)


class PadronClient:
	def __init__(self, url: str, transport: SoapTransport, auth: Auth) -> None:
		self.url, self.transport, self.auth = url, transport, auth

	def get_taxpayer(self, cuit: str) -> Taxpayer:
		cuit = normalize_cuit(cuit)
		body = (
			"<per:getPersona>"
			f"<sign>{escape(self.auth.sign)}</sign>"
			f"<token>{escape(self.auth.token)}</token>"
			f"<cuitRepresentada>{int(self.auth.cuit)}</cuitRepresentada>"
			f"<idPersona>{int(cuit)}</idPersona>"
			"</per:getPersona>"
		)
		try:
			response = self.transport.call(self.url, body, {"per": PADRON_NS})
		except ServiceFault as fault:
			if "no existe persona" in fault.message.lower():
				raise TaxpayerNotFound(fault.message) from fault
			raise
		return parse_taxpayer(response)
