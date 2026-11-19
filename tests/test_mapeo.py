"""The account mapping, pure and testable without a site.

Two tables, both keyed by ``(company, account)``: one says whether a revenue
account is taxable, the other says what a tax row is. Neither stores the alícuota
id: it is derived from the tax row's own rate, so the two copies cannot disagree.

Only ``Gravado`` and ``IVA`` are usable in version 1. The other values exist so
the refusal can name the account and what it is configured as, instead of saying
"unmapped" for something the operator did configure correctly.
"""

import pytest

from arca_integration.core.comprobante import ComprobanteError
from arca_integration.core.mapeo import (
    TAX_NATURES,
    INCOME_NATURES,
    AccountMapping,
    tax_nature_for,
    income_nature_for,
    validate_tax_mapping,
    validate_income_mapping,
)

EMPRESA = "Demo SA"


def _mapeo() -> AccountMapping:
    return AccountMapping.from_rows(
        income_rows=[
            {"company": EMPRESA, "account": "Ventas Gravadas", "naturaleza": "Gravado", "activo": 1},
            {"company": EMPRESA, "account": "Ventas Exentas", "naturaleza": "Exento", "activo": 1},
            {"company": EMPRESA, "account": "Ventas Viejas", "naturaleza": "Gravado", "activo": 0},
            {"company": "Otra SA", "account": "Ventas Gravadas", "naturaleza": "No gravado", "activo": 1},
        ],
        tax_rows=[
            {"company": EMPRESA, "account": "IVA 21%", "naturaleza": "IVA", "activo": 1},
            {"company": EMPRESA, "account": "Percepción IIBB", "naturaleza": "Percepcion", "activo": 1},
        ],
    )


def test_the_nature_catalog_is_frozen():
    """The values themselves, so a silent change is visible in one place.

    That the DocType stores these same values is checked in
    ``test_arca_mapeo_doctypes.py``; this one pins the catalog.
    """
    assert INCOME_NATURES == ("Gravado", "Exento", "No gravado")
    assert TAX_NATURES == ("IVA", "Percepcion", "Otro")


def test_from_rows_builds_both_tables_keyed_by_company_and_account():
    mapping = _mapeo()

    assert mapping.income_accounts[(EMPRESA, "Ventas Gravadas")] == "Gravado"
    assert mapping.tax_accounts[(EMPRESA, "IVA 21%")] == "IVA"


def test_an_inactive_mapping_is_not_used():
    """``activo`` is how an operator disables a row without deleting its history."""
    with pytest.raises(ComprobanteError, match="Ventas Viejas"):
        income_nature_for(_mapeo(), EMPRESA, "Ventas Viejas")


def test_the_same_account_in_another_company_is_another_mapping():
    assert income_nature_for(_mapeo(), EMPRESA, "Ventas Gravadas") == "Gravado"
    assert (
        income_nature_for(_mapeo(), "Otra SA", "Ventas Gravadas") == "No gravado"
    )


def test_the_same_account_name_in_a_company_that_has_no_row_is_not_found():
    with pytest.raises(ComprobanteError, match="Tercera SA"):
        income_nature_for(_mapeo(), "Tercera SA", "Ventas Gravadas")


def test_income_nature_for_returns_what_the_row_says():
    assert income_nature_for(_mapeo(), EMPRESA, "Ventas Exentas") == "Exento"


def test_an_unmapped_income_account_names_the_account():
    with pytest.raises(ComprobanteError, match="Alquileres"):
        income_nature_for(_mapeo(), EMPRESA, "Alquileres")


def test_tax_nature_for_returns_what_the_row_says():
    assert tax_nature_for(_mapeo(), EMPRESA, "IVA 21%") == "IVA"
    assert tax_nature_for(_mapeo(), EMPRESA, "Percepción IIBB") == "Percepcion"


def test_an_unmapped_tax_account_names_the_account():
    with pytest.raises(ComprobanteError, match="Ingresos Brutos"):
        tax_nature_for(_mapeo(), EMPRESA, "Ingresos Brutos")


def test_validate_income_mapping_accepts_an_income_account():
    validate_income_mapping("Ventas Gravadas", "Gravado", "Income")


def test_validate_income_mapping_refuses_a_non_income_account():
    with pytest.raises(ComprobanteError, match="Gastos"):
        validate_income_mapping("Gastos", "Gravado", "Expense")


def test_validate_income_mapping_refuses_a_nature_outside_the_select():
    with pytest.raises(ComprobanteError, match="Dudoso"):
        validate_income_mapping("Ventas", "Dudoso", "Income")


def test_validate_tax_mapping_accepts_every_documented_nature():
    for naturaleza in TAX_NATURES:
        validate_tax_mapping("Cuenta", naturaleza)


def test_validate_tax_mapping_refuses_a_nature_outside_the_select():
    with pytest.raises(ComprobanteError, match="Gravado"):
        validate_tax_mapping("Cuenta", "Gravado")


# --- duplicates --------------------------------------------------------------
#
# Two active rows for one account would make the emission depend on the order
# the rows came back in, so it is a refusal that names the account instead.


def test_two_active_rows_for_the_same_account_are_refused():
    with pytest.raises(ComprobanteError) as excinfo:
        AccountMapping.from_rows(
            income_rows=[
                {"company": EMPRESA, "account": "Ventas", "naturaleza": "Gravado"},
                {"company": EMPRESA, "account": "Ventas", "naturaleza": "Exento"},
            ]
        )

    assert "Ventas" in str(excinfo.value)
    assert EMPRESA in str(excinfo.value)


def test_an_inactive_duplicate_does_not_collide():
    mapping = AccountMapping.from_rows(
        income_rows=[
            {"company": EMPRESA, "account": "Ventas", "naturaleza": "Gravado"},
            {"company": EMPRESA, "account": "Ventas", "naturaleza": "Exento", "activo": 0},
        ]
    )

    assert mapping.income_accounts[(EMPRESA, "Ventas")] == "Gravado"


def test_the_same_account_in_two_companies_is_not_a_duplicate():
    mapping = AccountMapping.from_rows(
        income_rows=[
            {"company": EMPRESA, "account": "Ventas", "naturaleza": "Gravado"},
            {"company": "Otra SA", "account": "Ventas", "naturaleza": "Exento"},
        ]
    )

    assert mapping.income_accounts[(EMPRESA, "Ventas")] == "Gravado"
    assert mapping.income_accounts[("Otra SA", "Ventas")] == "Exento"
