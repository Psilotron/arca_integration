"""The money of one ``Sales Invoice``, as ARCA needs to receive it.

``ImpNeto``, ``ImpIVA`` and the ``Iva`` detail are not derivable from the invoice's
totals: ERPNext stores money per account and per tax row. This module maps the
invoice to the four amounts and the single alícuota that version 1 can emit, and
refuses, with a message that names the cause, everything it cannot represent
faithfully.

Two rules hold the design together.

The numbers are ERPNext's. ``net_amount`` already carries the line discount and
``tax_amount`` already carries the row's rounding, so nothing is recomputed and
nothing is distributed: a residual that does not close is refused, never absorbed
into an alícuota.

The function is pure. It takes the company currency as a parameter because
``Sales Invoice`` has no ``company_currency`` field in ERPNext v15, and it reads
``add_deduct_tax`` only when the row has it, because v15 removed the field from
``Sales Taxes and Charges``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping

from .comprobante import (
    TOLERANCIA_ABSOLUTA,
    AlicuotaImporte,
    ComprobanteError,
    Importes,
    validate_amounts,
)
from .mapeo import AccountMapping, income_nature_for, tax_nature_for
from .padron import normalize_cuit
from .wsfe.alicuotas import id_for_rate

CENTAVO = Decimal("0.01")

#: ARCA's document types. ``80`` is the CUIT and ``99`` its published
#: "unidentified final consumer", which always travels with number ``0``.
DOC_TIPO_CUIT = 80
DOC_TIPO_SIN_IDENTIFICAR = 99


def receiver_document(tax_id: str | None) -> tuple[int, int]:
    """The receiver's document type and number, from the invoice's tax id.

    An empty tax id is the unidentified consumer, which is what a Factura B to a
    walk-in customer carries. A value that is *not* a CUIT is refused instead of
    being sent as one: ARCA validates the number, and dropping it silently would
    turn an identified receiver into an anonymous one.
    """
    tax_id_text = (tax_id or "").strip()
    if not tax_id_text:
        return DOC_TIPO_SIN_IDENTIFICAR, 0

    try:
        return DOC_TIPO_CUIT, int(normalize_cuit(tax_id_text))
    except ValueError:
        raise ComprobanteError(
            f"El identificador fiscal del cliente, {tax_id_text!r}, no es un CUIT. La versión 1 "
            "solo "
            "informa CUIT o consumidor final sin identificar: corregí el identificador fiscal "
            "del cliente, o dejalo vacío y completá el documento del receptor en "
            "el comprobante."
        ) from None


def invoice_amounts(
    doc, mapping: AccountMapping, *, company_currency: str
) -> Importes:
    """Derive the ARCA amounts of one invoice, or refuse it.

    ``doc`` is a ``Sales Invoice`` or its ``as_dict()``: anything that exposes
    ``get`` or the fields as attributes. ``company_currency`` is
    ``Company.default_currency``, passed in because the invoice does not carry it.
    """
    validate_currency(doc, company_currency)
    validate_operation_type(doc)
    validate_global_discount(doc)
    validate_rounding(doc)

    company = str(field_value(doc, "company") or "")
    base = taxable_base(doc, mapping, company)
    net_total = as_decimal(field_value(doc, "net_total"))

    if base != net_total:
        raise ComprobanteError(
            f"Las líneas suman {format_amount(base)} de importe neto y la factura "
            f"informa {format_amount(net_total)}. El neto que viaja a ARCA tiene "
            "que ser el de las "
            "líneas, porque es la base sobre la que se calculó el IVA."
        )

    fila, cuenta, rate, importe = iva_row(doc, mapping, company)
    alicuota = id_for_rate(rate)

    esperado = base * rate / 100
    if abs(esperado - importe) > TOLERANCIA_ABSOLUTA:
        raise ComprobanteError(
            f"La fila de IVA de {cuenta!r} informa {format_amount(importe)} sobre "
            f"una base de {format_amount(base)} al {format_rate(rate)} %, y esa "
            f"combinación da {format_amount(esperado)}. "
            "La diferencia supera la tolerancia de 0.01 y no se corrige acá: "
            "revisá el IVA de la factura."
        )

    total = as_decimal(field_value(doc, "grand_total"))
    suma = base + importe
    if cents(suma) != cents(total):
        raise ComprobanteError(
            f"Los importes no cierran: neto {format_amount(base)} + IVA "
            f"{format_amount(importe)} = {format_amount(suma)}, y el total de la "
            f"factura es {format_amount(total)}. ARCA valida "
            "esa suma y la rechazaría con 10048."
        )

    amounts = Importes(
        neto=base,
        iva=importe,
        trib=Decimal("0.00"),
        total=total,
        alicuotas=(
            AlicuotaImporte(id=alicuota, base=base, importe=importe),
        ),
    )
    validate_amounts(amounts)
    return amounts


def validate_currency(doc, company_currency: str) -> None:
    moneda = str(field_value(doc, "currency") or "")
    if moneda != company_currency:
        raise ComprobanteError(
            f"La factura está en {moneda or 'sin moneda'} y la compañía en "
            f"{company_currency}. La versión 1 solo emite comprobantes en la "
            "moneda de la compañía."
        )

    cotizacion = as_decimal(field_value(doc, "conversion_rate", 1))
    if cotizacion != Decimal("1"):
        raise ComprobanteError(
            f"La factura tiene una cotización de {cotizacion} y la versión 1 solo "
            "emite con cotización 1: los importes en otra moneda no están "
            "modelados."
        )


def validate_operation_type(doc) -> None:
    if as_integer(field_value(doc, "is_return")):
        raise ComprobanteError(
            "La factura es una nota de crédito. La versión 1 no emite notas de "
            "crédito: necesitan CbtesAsoc y un tipo de comprobante distinto."
        )


def validate_global_discount(doc) -> None:
    descuento = as_decimal(field_value(doc, "discount_amount"))
    if descuento != 0:
        raise ComprobanteError(
            f"La factura tiene un descuento global de "
            f"{format_amount(descuento)}. ARCA recibe "
            "el neto gravado y el IVA por separado, así que el descuento no puede "
            "aplicarse sobre el total. Registralo como bonificación en las líneas."
        )


def validate_rounding(doc) -> None:
    ajuste = as_decimal(field_value(doc, "rounding_adjustment"))
    if ajuste != 0:
        raise ComprobanteError(
            f"La factura tiene un ajuste de redondeo de "
            f"{format_amount(ajuste)}. ARCA no tiene "
            "campo para informarlo, y un renglón sintético sería el mismo dato "
            "falso."
        )

    total = as_decimal(field_value(doc, "grand_total"))
    redondeado = as_decimal(field_value(doc, "rounded_total"))
    # ERPNext stores 0 when ``disable_rounded_total`` is on; that is not a
    # rounding difference, it is the absence of one.
    if redondeado not in (Decimal("0"), total):
        raise ComprobanteError(
            f"El total redondeado es {format_amount(redondeado)} y el total es "
            f"{format_amount(total)}. "
            "ARCA no recibe un total distinto del que surge de los componentes."
        )


def taxable_base(doc, mapping: AccountMapping, company: str) -> Decimal:
    invoice_lines = list(field_value(doc, "items") or ())
    if not invoice_lines:
        raise ComprobanteError(
            "La factura no tiene líneas, así que no hay importe neto que informar."
        )

    base = Decimal("0")
    for line in invoice_lines:
        line_index = field_value(line, "idx")
        account = str(field_value(line, "income_account") or "")
        if not account:
            raise ComprobanteError(
                f"La línea {line_index} no tiene cuenta de ingreso. No se usa la cuenta "
                "por defecto de la compañía porque el mapeo nunca la vio: elegí "
                "la cuenta en la línea."
            )

        nature = income_nature_for(mapping, company, account)
        if nature != "Gravado":
            raise ComprobanteError(
                f"La cuenta {account!r} está configurada como {nature} y la "
                "versión 1 no emite operaciones exentas ni no gravadas: necesitan "
                "ImpOpEx, que no está modelado."
            )

        base += as_decimal(field_value(line, "net_amount"))

    return base


def iva_row(doc, mapping: AccountMapping, company: str):
    tax_rows = list(field_value(doc, "taxes") or ())
    iva_rows = []

    for tax_row in tax_rows:
        account = str(field_value(tax_row, "account_head") or "")
        if not account:
            raise ComprobanteError(
                f"La fila de impuestos {field_value(tax_row, 'idx')} no tiene "
                "cuenta. "
                "Sin cuenta no se puede saber qué representa."
            )

        nature = tax_nature_for(mapping, company, account)
        if nature == "IVA":
            iva_rows.append((tax_row, account))
        else:
            raise ComprobanteError(
                f"La fila de impuestos de la cuenta {account!r} está mapeada "
                f"como {nature}. La versión 1 solo emite IVA y no informa "
                "percepciones ni otros tributos."
            )

    if not iva_rows:
        raise ComprobanteError(
            "La factura no tiene ninguna fila de IVA. La versión 1 emite el IVA "
            "como alícuota y no puede informarlo en cero sin una fila que lo "
            "represente."
        )

    if len(iva_rows) > 1:
        rates = ", ".join(
            format_rate(as_decimal(field_value(row, "rate")))
            for row, _ in iva_rows
        )
        raise ComprobanteError(
            f"La factura tiene más de una alícuota de IVA ({rates}). La versión 1 "
            "emite una sola alícuota por comprobante."
        )

    tax_row, account = iva_rows[0]
    validate_iva_row(tax_row, account)

    return (
        tax_row,
        account,
        as_decimal(field_value(tax_row, "rate")),
        as_decimal(field_value(tax_row, "tax_amount")),
    )


def validate_iva_row(row, account: str) -> None:
    charge_type = str(field_value(row, "charge_type") or "")
    if charge_type != "On Net Total":
        raise ComprobanteError(
            f"La fila de IVA de {account!r} usa el tipo de cálculo "
            f"{charge_type!r}. La "
            "versión 1 solo acepta 'On Net Total', la única que tiene una base "
            "informable."
        )

    add_deduct_tax = field_value(row, "add_deduct_tax")
    if add_deduct_tax is not None and add_deduct_tax != "Add":
        raise ComprobanteError(
            f"La fila de IVA de {account!r} tiene {add_deduct_tax!r} en "
            "add_deduct_tax. ARCA "
            "recibe el IVA sumando, nunca restando."
        )

    if as_integer(field_value(row, "included_in_print_rate")):
        raise ComprobanteError(
            f"El IVA de la fila de {account!r} está incluido en el precio. La "
            "versión 1 necesita un IVA discriminado para informar base e importe "
            "por separado."
        )


def field_value(doc, field_name: str, default=None):
    """Read a field from a mapping or from a document that exposes attributes."""
    if isinstance(doc, Mapping):
        return doc.get(field_name, default)
    return getattr(doc, field_name, default)


def as_decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ComprobanteError(f"El valor {value!r} no es un importe válido.")


def as_integer(value) -> int:
    if value is None or value == "":
        return 0
    return int(value)


def cents(value: Decimal) -> Decimal:
    return (value * 100).quantize(Decimal("1"))


def format_amount(value: Decimal) -> str:
    return f"{value:.2f}"


def format_rate(value: Decimal) -> str:
    """Spanish decimal separator, without a trailing ``.0``."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")
