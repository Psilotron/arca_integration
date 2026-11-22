"""The QR printed on electronic vouchers (ARCA, "Especificaciones del QR", version 1).

The QR encodes `{QR_URL}?p={payload}`: the payload is the voucher as compact
JSON in Base64. Numbers go as JSON numbers, in the order of the specification,
so the official example gives the published Base64 exactly. `parse` reads the
QR of a received voucher, from any issuer.
"""

import base64
import binascii
import io
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote, urlsplit

QR_URL = "https://www.arca.gob.ar/fe/qr/"
VERSION = 1
CAE = "E"  # tipoCodAut: authorized with a CAE (A would be CAEA)
CAEA = "A"


def _number(value: Decimal | int) -> int | float:
	"""12100 for 12100.00, 121.5 for 121.50: as the official example writes them."""
	value = Decimal(value)
	return int(value) if value == value.to_integral_value() else float(value)


def payload(
	*,
	issue_date: date,
	cuit: str,
	point_of_sale: int,
	voucher_type: int,
	number: int,
	total: Decimal,
	currency: str,
	exchange_rate: Decimal,
	cae: str,
	document_type: str = "",
	document_number: str = "",
) -> dict:
	data = {
		"ver": VERSION,
		"fecha": issue_date.isoformat(),
		"cuit": int(cuit),
		"ptoVta": int(point_of_sale),
		"tipoCmp": int(voucher_type),
		"nroCmp": int(number),
		"importe": _number(total),
		"moneda": currency,
		"ctz": _number(exchange_rate),
	}
	if document_type and document_number:
		data["tipoDocRec"] = int(document_type)
		data["nroDocRec"] = int(document_number)
	data["tipoCodAut"] = CAE
	data["codAut"] = int(cae)
	return data


def text(data: dict) -> str:
	"""What the QR encodes."""
	encoded = base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
	return f"{QR_URL}?p={encoded}"


def svg(content: str, scale: int = 3) -> str:
	"""The QR as an SVG document."""
	import pyqrcode

	buffer = io.BytesIO()
	pyqrcode.create(content, error="M").svg(buffer, scale=scale, xmldecl=False, svgns=True, omithw=False)
	return buffer.getvalue().decode()


class InvalidQr(ValueError):
	"""The text is not an ARCA voucher QR."""


@dataclass(frozen=True)
class QrVoucher:
	"""What the QR of a voucher says about it."""

	issue_date: date
	issuer_cuit: str
	point_of_sale: int
	voucher_type: int
	number: int
	total: Decimal
	currency: str
	exchange_rate: Decimal
	authorization_mode: str  # CAE or CAEA
	authorization_code: str
	receiver_document_type: str = ""
	receiver_document_number: str = ""


def parse(content: str) -> QrVoucher:
	"""Read the QR of a voucher: the scanned or pasted link (`...?p=...`) or its payload."""
	text = (content or "").strip()
	if "?" in text or text.lower().startswith("http"):
		encoded = parse_qs(urlsplit(text).query).get("p", [""])[0]
	else:
		encoded = unquote(text)
	if not encoded:
		raise InvalidQr("The link has no voucher data (p=...).")
	try:
		# A pasted link may turn "+" into spaces; some issuers use the URL-safe alphabet.
		standard = encoded.replace(" ", "+").replace("-", "+").replace("_", "/")
		data = json.loads(base64.b64decode(standard + "=" * (-len(standard) % 4)))
	except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
		raise InvalidQr("The QR data is not readable.") from exc
	if not isinstance(data, dict):
		raise InvalidQr("The QR data is not readable.")
	try:
		return QrVoucher(
			issue_date=date.fromisoformat(str(data["fecha"])[:10]),
			issuer_cuit=str(int(data["cuit"])),
			point_of_sale=int(data["ptoVta"]),
			voucher_type=int(data["tipoCmp"]),
			number=int(data["nroCmp"]),
			total=Decimal(str(data["importe"])),
			currency=str(data.get("moneda") or "PES"),
			exchange_rate=Decimal(str(data.get("ctz") or 1)),
			authorization_mode="CAEA" if str(data.get("tipoCodAut", CAE)).upper() == CAEA else "CAE",
			authorization_code=str(int(data["codAut"])),
			receiver_document_type=str(int(data["tipoDocRec"])) if data.get("tipoDocRec") else "",
			receiver_document_number=str(int(data["nroDocRec"])) if data.get("nroDocRec") else "",
		)
	except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
		raise InvalidQr("The QR lacks voucher data.") from exc
