"""Serialize a `Voucher` into `comprobanteCAERequest`, in the order ComprobanteType declares."""

from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from .voucher import Voucher, VoucherItem


def _value(value) -> str:
	if isinstance(value, Decimal):
		text = format(value, "f")
		return text.rstrip("0").rstrip(".") if "." in text else text
	if isinstance(value, date):
		return value.isoformat()
	return escape(str(value))


def _amount(value: Decimal) -> str:
	return format(value.quantize(Decimal("0.01")), "f")


def _elements(pairs) -> str:
	"""<name>value</name> for each pair, skipping None, False and empty values (not 0)."""
	return "".join(
		f"<{name}>{value}</{name}>"
		for name, value in pairs
		if value is not None and value is not False and value != ""
	)


def item_xml(item: VoucherItem) -> str:
	return (
		"<item>"
		+ _elements(
			[
				("unidadesMtx", item.gtin_units),
				("codigoMtx", escape(item.gtin)),
				("codigo", escape(item.code[:50])),
				("descripcion", escape(item.description)),
				("cantidad", item.quantity is not None and _value(item.quantity)),
				("codigoUnidadMedida", item.unit),
				("precioUnitario", item.unit_price is not None and _value(item.unit_price)),
				("codigoCondicionIVA", item.vat_condition),
				("importeIVA", item.vat_amount is not None and _amount(item.vat_amount)),
				("importeItem", _amount(item.amount)),
			]
		)
		+ "</item>"
	)


def voucher_xml(voucher: Voucher) -> str:
	if voucher.number is None:
		raise ValueError("The voucher number is assigned before serializing")

	other_taxes = "".join(
		"<otroTributo>"
		+ _elements(
			[
				("codigo", tax.tax_type),
				("descripcion", escape(tax.description)),
				("baseImponible", _amount(tax.base)),
				("importe", _amount(tax.amount)),
			]
		)
		+ "</otroTributo>"
		for tax in voucher.other_taxes
	)
	vat_subtotals = "".join(
		f"<subtotalIVA><codigo>{subtotal.vat_condition}</codigo><importe>{_amount(subtotal.amount)}</importe></subtotalIVA>"
		for subtotal in voucher.vat_subtotals
	)
	return (
		"<comprobanteCAERequest>"
		+ _elements(
			[
				("codigoTipoComprobante", voucher.voucher_type),
				("numeroPuntoVenta", voucher.point_of_sale),
				("numeroComprobante", voucher.number),
				("fechaEmision", _value(voucher.issue_date)),
				("codigoTipoDocumento", voucher.document_type),
				("numeroDocumento", voucher.document_number),
				("condicionIVAReceptor", voucher.receiver_vat_condition),
				("importeGravado", _amount(voucher.taxed)),
				("importeNoGravado", _amount(voucher.untaxed)),
				("importeExento", _amount(voucher.exempt)),
				("importeSubtotal", _amount(voucher.subtotal)),
				("importeOtrosTributos", voucher.other_taxes and _amount(voucher.other_taxes_total)),
				("importeTotal", _amount(voucher.total)),
				("codigoMoneda", escape(voucher.currency)),
				("cotizacionMoneda", _value(voucher.exchange_rate)),
				("observaciones", escape(voucher.notes)),
				("codigoConcepto", int(voucher.concept)),
				("fechaServicioDesde", voucher.service_start and _value(voucher.service_start)),
				("fechaServicioHasta", voucher.service_end and _value(voucher.service_end)),
				("fechaVencimientoPago", voucher.payment_due and _value(voucher.payment_due)),
				("arrayOtrosTributos", other_taxes),
				("arrayItems", "".join(item_xml(item) for item in voucher.items)),
				("arraySubtotalesIVA", vat_subtotals),
			]
		)
		+ "</comprobanteCAERequest>"
	)
