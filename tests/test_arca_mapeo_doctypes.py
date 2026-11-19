"""The two mapping DocTypes, checked without a bench.

They are configuration, so the schema is where the engine's assumptions live: the
account is a link, the nature is one of the values the core understands, and the
tax table has no rate of its own.
"""

import json
from pathlib import Path

from arca_integration.core.mapeo import (
    TAX_NATURES,
    INCOME_NATURES,
)

ROOT = (
    Path(__file__).parents[1] / "arca_integration" / "arca_integration" / "doctype"
)

INGRESO = "arca_mapeo_de_cuenta_de_ingreso"
IMPUESTO = "arca_mapeo_de_cuenta_de_impuesto"


def _document(name: str) -> dict:
    return json.loads((ROOT / name / f"{name}.json").read_text())


def _fields(name: str) -> dict:
    return {field["fieldname"]: field for field in _document(name)["fields"]}


def test_both_are_regular_documents_with_a_stable_name():
    for name, title in (
        (INGRESO, "ARCA Mapeo de Cuenta de Ingreso"),
        (IMPUESTO, "ARCA Mapeo de Cuenta de Impuesto"),
    ):
        doc = _document(name)

        assert doc["name"] == title
        assert doc["module"] == "Arca Integration"
        assert doc["autoname"] == "hash"
        assert not doc.get("istable")


def test_both_have_the_same_four_fields():
    for name in (INGRESO, IMPUESTO):
        assert list(_document(name)["field_order"]) == [
            "company",
            "account",
            "naturaleza",
            "activo",
        ]
        names = [field["fieldname"] for field in _document(name)["fields"]]

        assert names == list(_document(name)["field_order"])


def test_company_and_account_are_required_links():
    for name in (INGRESO, IMPUESTO):
        fields = _fields(name)

        assert fields["company"]["fieldtype"] == "Link"
        assert fields["company"]["options"] == "Company"
        assert fields["company"].get("reqd") == 1
        assert fields["account"]["fieldtype"] == "Link"
        assert fields["account"]["options"] == "Account"
        assert fields["account"].get("reqd") == 1


def test_the_nature_options_are_the_ones_the_core_understands():
    ingreso = _fields(INGRESO)["naturaleza"]
    impuesto = _fields(IMPUESTO)["naturaleza"]

    assert ingreso["fieldtype"] == "Select"
    assert ingreso["options"].split("\n") == list(INCOME_NATURES)
    assert ingreso.get("reqd") == 1
    assert impuesto["fieldtype"] == "Select"
    assert impuesto["options"].split("\n") == list(TAX_NATURES)
    assert impuesto.get("reqd") == 1


def test_activo_is_a_check_that_defaults_to_active():
    for name in (INGRESO, IMPUESTO):
        field = _fields(name)["activo"]

        assert field["fieldtype"] == "Check"
        assert field["default"] == "1"


def test_the_tax_mapping_does_not_store_a_rate_or_an_alicuota_id():
    """The id is derived from the tax row; a second copy could disagree."""
    fields = _fields(IMPUESTO)

    assert "rate" not in fields
    assert "alicuota" not in fields
    assert "alicuota_id" not in fields


def test_accounts_managers_can_write_and_accounts_users_can_only_read():
    for name in (INGRESO, IMPUESTO):
        permissions = {
            permission["role"]: permission for permission in _document(name)["permissions"]
        }

        assert permissions["System Manager"]["create"] == 1
        assert permissions["System Manager"]["write"] == 1
        assert permissions["Accounts Manager"]["create"] == 1
        assert permissions["Accounts Manager"]["write"] == 1
        assert permissions["Accounts User"]["read"] == 1
        assert not permissions["Accounts User"].get("write")


def test_each_controller_delegates_to_its_pure_validator():
    ingreso = (ROOT / INGRESO / f"{INGRESO}.py").read_text()
    impuesto = (ROOT / IMPUESTO / f"{IMPUESTO}.py").read_text()

    assert "validate_income_mapping" in ingreso
    assert "def validate(self)" in ingreso
    assert "validate_tax_mapping" in impuesto
    assert "def validate(self)" in impuesto


def test_the_account_picker_is_filtered_by_the_chosen_company():
    """An account belongs to one company; offering the others' is a trap.

    The filter is client-side and silently does nothing when the script is
    missing, so the file and the filter are both checked.
    """
    for name in (INGRESO, IMPUESTO):
        script = (ROOT / name / f"{name}.js").read_text()

        assert "set_query" in script
        assert '"account"' in script
        assert "company: frm.doc.company" in script
