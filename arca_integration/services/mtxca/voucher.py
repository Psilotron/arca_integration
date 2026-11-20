"""The voucher sent to `autorizarComprobante` (ComprobanteType), as plain data.

Field names are English; the comments give the WSMTXCA element each one maps to.
Amounts are `Decimal`, already rounded as ARCA expects (2 decimals, 6 for unit
prices and quantities).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import IntEnum


class Concept(IntEnum):
	"""codigoConcepto."""

	PRODUCTS = 1
	SERVICES = 2
	PRODUCTS_AND_SERVICES = 3


class Letter:
	A = "A"
	B = "B"


class Kind:
	INVOICE = "invoice"
	DEBIT_NOTE = "debit_note"
	CREDIT_NOTE = "credit_note"


# codigoTipoComprobante by letter and kind (manual p. 288).
VOUCHER_TYPES = {
	Letter.A: {Kind.INVOICE: 1, Kind.DEBIT_NOTE: 2, Kind.CREDIT_NOTE: 3},
	Letter.B: {Kind.INVOICE: 6, Kind.DEBIT_NOTE: 7, Kind.CREDIT_NOTE: 8},
}
LETTER_OF = {code: letter for letter, kinds in VOUCHER_TYPES.items() for code in kinds.values()}

# Document type 80 (CUIT).
CUIT = "80"

# codigoCondicionIVA without a rate: reported as untaxed or exempt, never in a VAT subtotal.
UNTAXED, EXEMPT = "1", "2"


@dataclass(frozen=True)
class VoucherItem:
	description: str  # descripcion
	unit: str  # codigoUnidadMedida
	vat_condition: str  # codigoCondicionIVA
	amount: Decimal  # importeItem
	quantity: Decimal | None = None  # cantidad
	unit_price: Decimal | None = None  # precioUnitario (without VAT in A, with VAT in B)
	vat_amount: Decimal | None = None  # importeIVA (class A only)
	code: str = ""  # codigo
	gtin: str = ""  # codigoMtx
	gtin_units: int | None = None  # unidadesMtx


@dataclass(frozen=True)
class VatSubtotal:
	vat_condition: str  # codigo
	amount: Decimal  # importe


@dataclass(frozen=True)
class OtherTax:
	tax_type: str  # codigo
	description: str  # descripcion
	base: Decimal  # baseImponible
	amount: Decimal  # importe


@dataclass(frozen=True)
class Voucher:
	voucher_type: int  # codigoTipoComprobante
	point_of_sale: int  # numeroPuntoVenta
	issue_date: date  # fechaEmision
	receiver_vat_condition: str  # condicionIVAReceptor
	concept: Concept  # codigoConcepto
	currency: str  # codigoMoneda
	exchange_rate: Decimal  # cotizacionMoneda
	items: tuple[VoucherItem, ...]  # arrayItems
	taxed: Decimal  # importeGravado
	untaxed: Decimal  # importeNoGravado
	exempt: Decimal  # importeExento
	subtotal: Decimal  # importeSubtotal
	other_taxes_total: Decimal  # importeOtrosTributos
	total: Decimal  # importeTotal
	vat_subtotals: tuple[VatSubtotal, ...] = ()  # arraySubtotalesIVA
	other_taxes: tuple[OtherTax, ...] = ()  # arrayOtrosTributos
	document_type: str = ""  # codigoTipoDocumento
	document_number: str = ""  # numeroDocumento
	service_start: date | None = None  # fechaServicioDesde
	service_end: date | None = None  # fechaServicioHasta
	payment_due: date | None = None  # fechaVencimientoPago
	number: int | None = None  # numeroComprobante, assigned right before authorizing
	notes: str = ""  # observaciones

	@property
	def letter(self) -> str:
		return LETTER_OF[self.voucher_type]

	@property
	def vat_total(self) -> Decimal:
		return sum((subtotal.amount for subtotal in self.vat_subtotals), Decimal(0))
