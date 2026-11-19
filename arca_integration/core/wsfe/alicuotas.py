"""ARCA's IVA catalog, measured rather than documented.

``FEParamGetTiposIva`` answered six ids against homologación on 2026-11-12. They
are the only rates a comprobante may carry in version 1: an invoice whose tax row
uses another rate is refused, because ARCA has no id for it and rounding it to the
nearest published one would change the fiscal document.

The catalog lives here, not in the DocType, because the same six values must be
available to the pure layer and to the schema test. The mapping tables deliberately
do **not** store the id: it is derived from the tax row's own rate, so the two
copies cannot disagree.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ..comprobante import ComprobanteError

#: ARCA id → percentage. Measured, not invented.
IVA_RATES: dict[int, Decimal] = {
    3: Decimal("0"),
    4: Decimal("10.5"),
    5: Decimal("21"),
    6: Decimal("27"),
    8: Decimal("5"),
    9: Decimal("2.5"),
}

#: Percentage → ARCA id, so a rate never has to be matched by iterating.
ID_BY_RATE: dict[Decimal, int] = {
    rate: rate_id for rate_id, rate in IVA_RATES.items()
}


def _as_decimal(value: object, field: str) -> Decimal:
    """Coerce a Frappe ``Float``, a string or a ``Decimal`` without float noise.

    ``Decimal(10.5)`` would carry the binary representation; ``Decimal(str(...))``
    reads the value ERPNext stored and printed.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ComprobanteError(
            f"El valor de {field} no es un número: {value!r}."
        )


def rate_for_id(rate_id: int) -> Decimal:
    """The rate that belongs to an ARCA id."""
    try:
        return IVA_RATES[int(rate_id)]
    except (KeyError, TypeError, ValueError):
        raise ComprobanteError(
            f"La alícuota {rate_id!r} no está en el catálogo de ARCA. "
            "El catálogo medido contra homologación es 3 = 0 %, 4 = 10.5 %, "
            "5 = 21 %, 6 = 27 %, 8 = 5 % y 9 = 2.5 %."
        )


def id_for_rate(rate: object) -> int:
    """The ARCA id for a tax row's rate, refused when ARCA has no id for it."""
    value = _as_decimal(rate, "la alícuota")
    try:
        return ID_BY_RATE[value]
    except KeyError:
        raise ComprobanteError(
            f"La tasa {value} % no corresponde a ninguna alícuota de ARCA. "
            "El catálogo medido contra homologación es 0 %, 2.5 %, 5 %, "
            "10.5 %, 21 % y 27 %."
        )


def validate_iva_rate(rate_id: int, percentage: object) -> None:
    """Refuse a row whose percentage contradicts its id.

    Two stored values that disagree are a bug in the caller, never an editing
    choice: the id is what travels to ARCA and the percentage is what gets printed,
    so they must describe the same rate.
    """
    expected = rate_for_id(rate_id)
    actual = _as_decimal(percentage, "el porcentaje")
    if actual != expected:
        raise ComprobanteError(
            f"La alícuota {rate_id} corresponde a {expected} % y la fila informa "
            f"{actual} %. El id y el porcentaje deben describir la misma tasa."
        )


def validate_iva_row(
    rate_id: int, percentage: object, base: object, amount: object
) -> None:
    """The whole check of one child row, callable without a bench.

    The controller is a thin adapter over this function, so the rule that protects
    the request can be exercised offline. Cross-row sums stay in
    ``validate_amounts``: they belong to the parent, not to a row.
    """
    validate_iva_rate(rate_id, percentage)

    base_amount = _as_decimal(base, "la base imponible")
    if base_amount < 0:
        raise ComprobanteError(
            f"La base imponible de una alícuota no puede ser negativa: {base_amount}."
        )

    tax_amount = _as_decimal(amount, "el importe")
    if tax_amount < 0:
        raise ComprobanteError(
            f"El importe de IVA de una alícuota no puede ser negativo: {tax_amount}."
        )
