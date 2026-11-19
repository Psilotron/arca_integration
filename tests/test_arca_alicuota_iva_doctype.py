"""The child that records the IVA detail sent to ARCA, checked without a bench.

``ARCA Alicuota IVA`` is a snapshot of the request, not a configuration: it exists
so the comprobante can be printed and so a readback can be compared field by
field. It must therefore be read-only, complete, and unable to drift from the
catalog in ``core/wsfe/alicuotas.py``.
"""

import json
import re
from pathlib import Path

from arca_integration.core.wsfe.alicuotas import IVA_RATES

DOCTYPE = (
    Path(__file__).parents[1]
    / "arca_integration"
    / "arca_integration"
    / "doctype"
    / "arca_alicuota_iva"
)


def _document() -> dict:
    return json.loads((DOCTYPE / "arca_alicuota_iva.json").read_text())


def _fields() -> dict:
    return {field["fieldname"]: field for field in _document()["fields"]}


def test_it_is_a_child_table_named_the_way_the_parent_links_it():
    doc = _document()

    assert doc["name"] == "ARCA Alicuota IVA"
    assert doc["module"] == "Arca Integration"
    assert doc["istable"] == 1
    assert doc["autoname"] == "hash"
    assert doc["permissions"] == []


def test_it_has_exactly_the_four_fields_of_one_alicuota_row():
    assert list(_document()["field_order"]) == [
        "alicuota",
        "porcentaje",
        "base_imp",
        "importe",
    ]


def test_field_order_and_fields_agree():
    doc = _document()
    names = [field["fieldname"] for field in doc["fields"]]

    assert names == list(doc["field_order"])
    assert len(set(names)) == len(names)


def test_every_field_is_required_and_read_only():
    """The engine writes these rows; a hand edit would misstate the IVA detail."""
    for name, field in _fields().items():
        assert field.get("reqd") == 1, name
        assert field.get("read_only") == 1, name


def test_the_types_are_the_ones_the_engine_stores():
    fields = _fields()

    assert fields["alicuota"]["fieldtype"] == "Int"
    assert fields["porcentaje"]["fieldtype"] == "Float"
    assert fields["porcentaje"]["precision"] == "2"
    assert fields["base_imp"]["fieldtype"] == "Currency"
    assert fields["importe"]["fieldtype"] == "Currency"


def test_the_description_names_every_id_in_the_measured_catalog():
    """A catalog change must reach the form, or the operator reads a stale list."""
    description = _fields()["alicuota"]["description"]

    for alicuota, porcentaje in IVA_RATES.items():
        assert re.search(rf"\b{alicuota}\s*=", description), alicuota
        assert str(porcentaje).replace(".", ",") in description.replace(".", ",")


def test_the_controller_delegates_to_the_pure_validator():
    """Without a bench we cannot run it; we can prove it is a thin adapter."""
    source = (DOCTYPE / "arca_alicuota_iva.py").read_text()

    assert "validate_iva_row" in source
    assert "def validate(self)" in source
