"""From a ``Sales Invoice`` to the money ARCA receives.

The numbers are ERPNext's, never recomputed: ``net_amount`` already carries the
line discount, ``tax_amount`` already carries the row's rounding. The function
only refuses what ARCA cannot receive, and refuses it with a message that names
the cause.

The fixtures are provisional until a bench captures them. See the README beside
them: a hand-written fixture tests my assumptions, a captured one tests ERPNext.
"""

import copy
import json
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from arca_integration.core.comprobante import ComprobanteError, AlicuotaImporte
from arca_integration.core.factura import invoice_amounts, receiver_document
from arca_integration.core.mapeo import AccountMapping

FIXTURES = Path(__file__).parent / "fixtures" / "sales_invoice"
EMPRESA = "Demo SA"
MONEDA = "ARS"

# The account names below are the ones the provisional fixtures carry. After a
# bench captures real invoices, replace the four captured ones with the values
# printed by the capture instructions; the two mapping-only names stay invented,
# because they only have to be absent from the captured invoices.
CUENTA_GRAVADA = "Ventas Gravadas"
CUENTA_EXENTA = "Ventas Exentas"
CUENTA_IVA_21 = "IVA 21%"
CUENTA_IVA_10_5 = "IVA 10,5%"
CUENTA_IVA_0 = "IVA 0%"
CUENTA_PERCEPCION = "Percepción IIBB"


def caso(letra: str) -> dict:
    return copy.deepcopy(json.loads((FIXTURES / f"caso_{letra}.json").read_text()))


def mapeo() -> AccountMapping:
    return AccountMapping.from_rows(
        income_rows=[
            {"company": EMPRESA, "account": CUENTA_GRAVADA, "naturaleza": "Gravado", "activo": 1},
            {"company": EMPRESA, "account": CUENTA_EXENTA, "naturaleza": "Exento", "activo": 1},
        ],
        tax_rows=[
            {"company": EMPRESA, "account": CUENTA_IVA_21, "naturaleza": "IVA", "activo": 1},
            {"company": EMPRESA, "account": CUENTA_IVA_10_5, "naturaleza": "IVA", "activo": 1},
            {"company": EMPRESA, "account": CUENTA_IVA_0, "naturaleza": "IVA", "activo": 1},
            {"company": EMPRESA, "account": CUENTA_PERCEPCION, "naturaleza": "Percepcion", "activo": 1},
        ],
    )


def importes(doc, *, moneda: str = MONEDA):
    return invoice_amounts(doc, mapeo(), company_currency=moneda)


def test_case_a_reports_the_single_twenty_one_percent_line():
    result = importes(caso("a"))

    assert result.neto == Decimal("1000.00")
    assert result.iva == Decimal("210.00")
    assert result.trib == Decimal("0.00")
    assert result.total == Decimal("1210.00")
    assert result.alicuotas == (
        AlicuotaImporte(id=5, base=Decimal("1000.00"), importe=Decimal("210.00")),
    )


def test_case_b_uses_the_ten_and_a_half_id():
    result = importes(caso("b"))

    assert result.alicuotas == (
        AlicuotaImporte(id=4, base=Decimal("2500.00"), importe=Decimal("262.50")),
    )


def test_case_c_accepts_the_sub_cent_residual_of_the_tax_row():
    """1999.99 × 21 % = 419.9979, and ERPNext stored 420.00. Inside 0.01."""
    result = importes(caso("c"))

    assert result.neto == Decimal("1999.99")
    assert result.iva == Decimal("420.00")
    assert result.alicuotas == (
        AlicuotaImporte(id=5, base=Decimal("1999.99"), importe=Decimal("420.00")),
    )


def test_case_d_reports_zero_percent_as_an_alicuota_with_zero_amount():
    result = importes(caso("d"))

    assert result.neto == Decimal("800.00")
    assert result.iva == Decimal("0.00")
    assert result.alicuotas == (
        AlicuotaImporte(id=3, base=Decimal("800.00"), importe=Decimal("0.00")),
    )


def test_case_e_refuses_a_global_discount_naming_the_amount():
    with pytest.raises(ComprobanteError, match="descuento global de 100.00"):
        importes(caso("e"))


def test_case_f_uses_net_amount_which_already_carries_the_line_discount():
    result = importes(caso("f"))

    assert result.neto == Decimal("900.00")
    assert result.alicuotas == (
        AlicuotaImporte(id=5, base=Decimal("900.00"), importe=Decimal("189.00")),
    )


def test_case_g_refuses_two_iva_rates_and_names_both():
    with pytest.raises(ComprobanteError, match="más de una alícuota") as error:
        importes(caso("g"))

    assert "21" in str(error.value)
    assert "10,5" in str(error.value)


def test_it_refuses_a_credit_note():
    doc = caso("a")
    doc["is_return"] = 1

    with pytest.raises(ComprobanteError, match="nota de crédito"):
        importes(doc)


def test_it_refuses_a_currency_that_is_not_the_company_currency():
    doc = caso("a")
    doc["currency"] = "USD"

    with pytest.raises(ComprobanteError, match="USD"):
        importes(doc)


def test_it_refuses_a_conversion_rate_other_than_one():
    doc = caso("a")
    doc["conversion_rate"] = 2

    with pytest.raises(ComprobanteError, match="cotización"):
        importes(doc)


def test_it_refuses_a_rounding_adjustment_naming_the_amount():
    doc = caso("a")
    doc["rounding_adjustment"] = 0.4
    doc["rounded_total"] = 1210.4

    with pytest.raises(ComprobanteError, match="redondeo"):
        importes(doc)


def test_it_refuses_a_rounded_total_that_is_not_the_grand_total():
    doc = caso("a")
    doc["rounded_total"] = 1200.00

    with pytest.raises(ComprobanteError, match="1200"):
        importes(doc)


def test_it_accepts_a_disabled_rounding_where_rounded_total_is_zero():
    doc = caso("a")
    doc["rounded_total"] = 0

    assert importes(doc).total == Decimal("1210.00")


def test_it_refuses_an_unmapped_income_account_naming_it():
    doc = caso("a")
    doc["items"][0]["income_account"] = "Alquileres"

    with pytest.raises(ComprobanteError, match="Alquileres"):
        importes(doc)


def test_it_refuses_an_account_mapped_as_exempt_naming_the_account_and_its_nature():
    doc = caso("a")
    doc["items"][0]["income_account"] = CUENTA_EXENTA

    with pytest.raises(ComprobanteError, match=re.escape(CUENTA_EXENTA)) as error:
        importes(doc)
    assert "Exento" in str(error.value)


def test_it_refuses_an_empty_income_account_naming_the_line():
    doc = caso("a")
    doc["items"][0]["income_account"] = ""

    with pytest.raises(ComprobanteError, match="[Ll]ínea 1"):
        importes(doc)


def test_it_refuses_an_unmapped_tax_account_naming_it():
    doc = caso("a")
    doc["taxes"][0]["account_head"] = "Ingresos Brutos"

    with pytest.raises(ComprobanteError, match="Ingresos Brutos"):
        importes(doc)


def test_it_refuses_a_perception_naming_the_account_and_its_nature():
    doc = caso("a")
    doc["taxes"][0]["account_head"] = CUENTA_PERCEPCION

    with pytest.raises(ComprobanteError, match=re.escape(CUENTA_PERCEPCION)) as error:
        importes(doc)
    assert "Percepcion" in str(error.value)


def test_it_refuses_a_charge_type_without_a_reportable_base():
    doc = caso("a")
    doc["taxes"][0]["charge_type"] = "Actual"

    with pytest.raises(ComprobanteError, match="Actual"):
        importes(doc)


def test_it_refuses_a_tax_included_in_the_print_rate():
    doc = caso("a")
    doc["taxes"][0]["included_in_print_rate"] = 1

    with pytest.raises(ComprobanteError, match="incluido"):
        importes(doc)


def test_it_refuses_a_deducted_tax_when_the_field_exists():
    doc = caso("a")
    doc["taxes"][0]["add_deduct_tax"] = "Deduct"

    with pytest.raises(ComprobanteError, match="Deduct"):
        importes(doc)


def test_it_tolerates_the_absent_add_deduct_tax_of_erpnext_v15():
    """v15 removed the field; its absence is not a deduction."""
    doc = caso("a")
    doc["taxes"][0].pop("add_deduct_tax")

    assert importes(doc).iva == Decimal("210.00")


def test_it_refuses_a_rate_arca_does_not_publish():
    doc = caso("a")
    doc["taxes"][0]["rate"] = 19.5
    doc["taxes"][0]["tax_amount"] = 195.00
    doc["grand_total"] = 1195.00
    doc["rounded_total"] = 1195.00

    with pytest.raises(ComprobanteError, match="19.5"):
        importes(doc)


def test_it_refuses_a_tax_amount_that_is_not_that_rate_on_that_base():
    doc = caso("a")
    doc["taxes"][0]["tax_amount"] = 300.00
    doc["grand_total"] = 1300.00
    doc["rounded_total"] = 1300.00

    with pytest.raises(ComprobanteError, match="300"):
        importes(doc)


def test_it_refuses_when_the_lines_do_not_sum_to_the_net_total():
    doc = caso("a")
    doc["net_total"] = 1001.00

    with pytest.raises(ComprobanteError, match="1001"):
        importes(doc)


def test_it_refuses_an_invoice_without_lines():
    doc = caso("a")
    doc["items"] = []
    doc["net_total"] = 0.00

    with pytest.raises(ComprobanteError, match="líneas"):
        importes(doc)


def test_it_refuses_an_invoice_without_a_tax_row():
    doc = caso("a")
    doc["taxes"] = []
    doc["total_taxes_and_charges"] = 0.00
    doc["grand_total"] = 1000.00
    doc["rounded_total"] = 1000.00

    with pytest.raises(ComprobanteError, match="IVA"):
        importes(doc)


def test_it_refuses_when_the_components_do_not_reproduce_the_total():
    doc = caso("a")
    doc["grand_total"] = 1300.00
    doc["rounded_total"] = 1300.00

    with pytest.raises(ComprobanteError, match="1300"):
        importes(doc)


def test_it_works_on_a_document_that_exposes_attributes_instead_of_keys():
    """``frappe.Document`` is not a mapping: the same function must accept both."""
    doc = _como_objeto(caso("a"))

    result = importes(doc)

    assert result.total == Decimal("1210.00")
    assert result.alicuotas[0].id == 5


def _como_objeto(valor):
    if isinstance(valor, dict):
        return SimpleNamespace(**{clave: _como_objeto(v) for clave, v in valor.items()})
    if isinstance(valor, list):
        return [_como_objeto(item) for item in valor]
    return valor


@pytest.mark.parametrize("letra", ["a", "b", "c", "d", "e", "f", "g"])
def test_every_fixture_declares_where_it_came_from(letra):
    doc = caso(letra)

    assert doc["_source"] in {"provisional", "captured"}
    assert doc["_caso"].lower() == letra


# --- the receiver's document -------------------------------------------------
#
# ARCA validates the document number it receives, so a value that is not a CUIT
# must never travel as one. ``99`` with ``0`` is its published "unidentified
# final consumer".


def test_a_cuit_tax_id_becomes_document_type_80():
    assert receiver_document("20-00000000-1") == (80, 20000000001)


def test_a_missing_tax_id_is_the_unidentified_consumer():
    assert receiver_document(None) == (99, 0)
    assert receiver_document("   ") == (99, 0)


def test_a_tax_id_that_is_not_a_cuit_is_refused_instead_of_dropped():
    with pytest.raises(ComprobanteError) as excinfo:
        receiver_document("12.345.678")

    assert "12.345.678" in str(excinfo.value)
