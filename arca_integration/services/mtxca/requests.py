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
	"""<name>value</name> for each pair whose value is not None or "" (0 is kept)."""
	for name, value in pairs:
		if not isinstance(value, str | int | None):
			raise TypeError(f"{name}: {value!r} is not serialized text")
	return "".join(f"<{name}>{value}</{name}>" for name, value in pairs if value is not None and value != "")


def item_xml(item: VoucherItem) -> str:
	return (
		"<item>"
		+ _elements(
			[
				("unidadesMtx", item.gtin_units),
				("codigoMtx", escape(item.gtin)),
				("codigo", escape(item.code[:50])),
				("descripcion", escape(item.description)),
				("cantidad", _value(item.quantity) if item.quantity is not None else None),
				("codigoUnidadMedida", item.unit),
				("precioUnitario", _value(item.unit_price) if item.unit_price is not None else None),
				("codigoCondicionIVA", item.vat_condition),
				("importeIVA", _amount(item.vat_amount) if item.vat_amount is not None else None),
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
				("importeOtrosTributos", _amount(voucher.other_taxes_total) if voucher.other_taxes else None),
				("importeTotal", _amount(voucher.total)),
				("codigoMoneda", escape(voucher.currency)),
				("cotizacionMoneda", _value(voucher.exchange_rate)),
				("observaciones", escape(voucher.notes)),
				("codigoConcepto", int(voucher.concept)),
				("fechaServicioDesde", _value(voucher.service_start) if voucher.service_start else None),
				("fechaServicioHasta", _value(voucher.service_end) if voucher.service_end else None),
				("fechaVencimientoPago", _value(voucher.payment_due) if voucher.payment_due else None),
				("arrayOtrosTributos", other_taxes),
				("arrayItems", "".join(item_xml(item) for item in voucher.items)),
				("arraySubtotalesIVA", vat_subtotals),
			]
		)
		+ "</comprobanteCAERequest>"
	)
