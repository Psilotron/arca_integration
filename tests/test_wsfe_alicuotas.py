"""ARCA's IVA catalog, as measured against homologación.

The catalog is not documentation: it is the six ids ``FEParamGetTiposIva``
answered on 2026-11-12. An invoice whose tax row carries a rate outside it cannot
be sent, because ARCA has no id for that rate and guessing the nearest one changes
the fiscal document.
"""

from decimal import Decimal

import pytest

from arca_integration.core.comprobante import ComprobanteError
from arca_integration.core.wsfe.alicuotas import (
    IVA_RATES,
    id_for_rate,
    rate_for_id,
    validate_iva_rate,
    validate_iva_row,
)


def test_the_catalog_is_the_one_measured_against_homologacion():
    assert IVA_RATES == {
        3: Decimal("0"),
        4: Decimal("10.5"),
        5: Decimal("21"),
        6: Decimal("27"),
        8: Decimal("5"),
        9: Decimal("2.5"),
    }


def test_rate_for_id_returns_the_rate():
    assert rate_for_id(3) == Decimal("0")
    assert rate_for_id(9) == Decimal("2.5")
    assert rate_for_id(5) == Decimal("21")


def test_rate_for_id_refuses_an_id_arca_does_not_publish():
    with pytest.raises(ComprobanteError, match="19"):
        rate_for_id(19)


def test_id_for_rate_matches_the_rate_the_tax_row_carries():
    assert id_for_rate(Decimal("0")) == 3
    assert id_for_rate(Decimal("2.5")) == 9
    assert id_for_rate(Decimal("5")) == 8
    assert id_for_rate(Decimal("10.5")) == 4
    assert id_for_rate(Decimal("21")) == 5
    assert id_for_rate(Decimal("27")) == 6


def test_id_for_rate_accepts_the_float_erpnext_stores():
    """``Sales Taxes and Charges.rate`` is a ``Float``; ``10.5`` must match."""
    assert id_for_rate(10.5) == 4
    assert id_for_rate(21.0) == 5
    assert id_for_rate("2.5") == 9


def test_id_for_rate_refuses_a_rate_outside_the_catalog():
    with pytest.raises(ComprobanteError, match="19.5"):
        id_for_rate(Decimal("19.5"))


def test_validate_iva_rate_accepts_the_pair_arca_publishes():
    validate_iva_rate(5, Decimal("21"))
    validate_iva_rate(3, 0)


def test_validate_iva_rate_refuses_a_percentage_that_contradicts_the_id():
    with pytest.raises(ComprobanteError, match="10.5"):
        validate_iva_rate(5, Decimal("10.5"))


def test_validate_iva_row_accepts_a_consistent_child_row():
    validate_iva_row(5, Decimal("21"), Decimal("1000"), Decimal("210"))


def test_validate_iva_row_refuses_a_negative_base():
    with pytest.raises(ComprobanteError, match="base"):
        validate_iva_row(5, Decimal("21"), Decimal("-1"), Decimal("0"))


def test_validate_iva_row_refuses_a_negative_amount():
    with pytest.raises(ComprobanteError, match="importe"):
        validate_iva_row(5, Decimal("21"), Decimal("1000"), Decimal("-210"))
