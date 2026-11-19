"""The two bench scripts, exercised with a stub ``frappe``.

Neither script can run here: they need a real site. What can be verified is the
part that decides *what* gets written — the field whitelist, the provenance mark,
the seven invoice definitions and the account lookup — because that is where a
mistake would produce a fixture that looks captured and is not.

The scripts are loaded from their files with a fake ``frappe`` in
``sys.modules``, so importing them never touches a bench.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

BASE = Path(__file__).parent / "fixtures" / "sales_invoice"
CAPTURAR = BASE / "capturar.py"
PREPARAR = BASE / "preparar.py"


class _Meta:
    def __init__(self, campos):
        self._campos = set(campos)

    def has_field(self, nombre):
        return nombre in self._campos


class _Doc(dict):
    """Enough of ``frappe.Document`` for both scripts: ``get``, ``meta``, ``insert``."""

    def __init__(self, datos, *, campos=(), hijos=None):
        super().__init__(datos)
        self.meta = _Meta(campos)
        for nombre, valor in (hijos or {}).items():
            self[nombre] = valor

    def insert(self):
        self.setdefault("name", "NUEVO-1")
        return self


def _cargar(nombre, ruta, frappe):
    previo = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location(nombre, ruta)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nombre] = modulo
        spec.loader.exec_module(modulo)
        return modulo
    finally:
        if previo is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previo


def _factura(nombre="SINV-0001", *, con_add_deduct=False):
    impuesto = {
        "idx": 1,
        "charge_type": "On Net Total",
        "account_head": "IVA 21% - TST",
        "rate": 21.0,
        "tax_amount": 210.0,
        "included_in_print_rate": 0,
    }
    campos = {"charge_type", "account_head", "rate", "tax_amount", "included_in_print_rate"}
    if con_add_deduct:
        impuesto["add_deduct_tax"] = "Add"
        campos.add("add_deduct_tax")

    return _Doc(
        {
            "name": nombre,
            "docstatus": 0,
            "company": "Demo SA",
            "currency": "ARS",
            "conversion_rate": 1.0,
            "posting_date": "2026-11-12",
            "is_return": 0,
            "discount_amount": 0.0,
            "rounding_adjustment": 0.0,
            "rounded_total": 1210.0,
            "net_total": 1000.0,
            "total_taxes_and_charges": 210.0,
            "grand_total": 1210.0,
        },
        hijos={
            "items": [
                _Doc(
                    {
                        "idx": 1,
                        "income_account": "Ventas Gravadas - TST",
                        "net_amount": 1000.0,
                        "item_tax_template": None,
                        "item_tax_rate": "{}",
                    }
                )
            ],
            "taxes": [_Doc(impuesto, campos=campos)],
        },
    )


def _stub_frappe(doc=None):
    frappe = types.ModuleType("frappe")
    frappe.get_doc = lambda doctype, name: doc
    return frappe


def test_extraer_keeps_only_the_documented_fields():
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe())

    data = capturar._extraer(_factura())

    assert set(data) == {
        "company",
        "currency",
        "conversion_rate",
        "posting_date",
        "is_return",
        "discount_amount",
        "rounding_adjustment",
        "rounded_total",
        "net_total",
        "total_taxes_and_charges",
        "grand_total",
        "items",
        "taxes",
    }
    assert "name" not in data
    assert "docstatus" not in data


def test_extraer_stringifies_the_posting_date_and_keeps_the_lines():
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe())

    data = capturar._extraer(_factura())

    assert data["posting_date"] == "2026-11-12"
    assert data["items"] == [
        {
            "idx": 1,
            "income_account": "Ventas Gravadas - TST",
            "net_amount": 1000.0,
            "item_tax_template": None,
            "item_tax_rate": "{}",
        }
    ]


def test_extraer_keeps_add_deduct_tax_only_when_the_site_has_the_field():
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe())

    sin_campo = capturar._extraer(_factura())
    con_campo = capturar._extraer(_factura(con_add_deduct=True))

    assert "add_deduct_tax" not in sin_campo["taxes"][0]
    assert con_campo["taxes"][0]["add_deduct_tax"] == "Add"


def test_capturar_marks_the_file_as_captured_and_creates_the_directory(tmp_path):
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe(_factura("SINV-7")))
    destino = tmp_path / "nuevo"

    capturar.capturar({"A": "SINV-7"}, destino=destino)

    data = json.loads((destino / "caso_a.json").read_text())
    assert data["_source"] == "captured"
    assert data["_caso"] == "A"
    assert data["_descripcion"]
    assert data["net_total"] == 1000.0


def test_capturar_refuses_to_overwrite_an_already_captured_file(tmp_path):
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe(_factura()))
    capturar.capturar({"A": "SINV-1"}, destino=tmp_path)

    with pytest.raises(SystemExit, match="ya está capturado"):
        capturar.capturar({"A": "SINV-1"}, destino=tmp_path)


def test_capturar_overwrites_a_provisional_file_without_asking(tmp_path):
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe(_factura()))
    (tmp_path / "caso_a.json").write_text(json.dumps({"_source": "provisional"}))

    capturar.capturar({"A": "SINV-1"}, destino=tmp_path)

    assert json.loads((tmp_path / "caso_a.json").read_text())["_source"] == "captured"


def test_capturar_overwrites_a_captured_file_when_told_to(tmp_path):
    capturar = _cargar("capturar", CAPTURAR, _stub_frappe(_factura()))
    capturar.capturar({"A": "SINV-1"}, destino=tmp_path)

    capturar.capturar({"A": "SINV-2"}, destino=tmp_path, sobrescribir=True)

    assert json.loads((tmp_path / "caso_a.json").read_text())["_source"] == "captured"


def _especificaciones():
    preparar = _cargar("preparar", PREPARAR, types.ModuleType("frappe"))
    return preparar.especificaciones(
        "Demo SA",
        "Cliente Prueba",
        "ARCA-TEST",
        ingreso="Ventas Gravadas",
        iva_21="IVA 21",
        iva_105="IVA 10.5",
        iva_0="IVA 0",
    )


def test_there_are_seven_invoice_definitions_for_the_seven_cases():
    assert set(_especificaciones()) == set("ABCDEFG")


def test_every_definition_is_a_draft_sales_invoice_with_a_fixed_date():
    for caso, spec in _especificaciones().items():
        assert spec["doctype"] == "Sales Invoice", caso
        assert spec["company"] == "Demo SA"
        assert spec["customer"] == "Cliente Prueba"
        assert spec["posting_date"] == "2026-11-12"
        assert "docstatus" not in spec


def test_case_a_is_one_thousand_at_twenty_one_percent():
    spec = _especificaciones()["A"]

    assert spec["items"] == [
        {"item_code": "ARCA-TEST", "qty": 1, "rate": 1000, "income_account": "Ventas Gravadas"}
    ]
    assert spec["taxes"][0]["rate"] == 21
    assert spec["taxes"][0]["account_head"] == "IVA 21"
    assert spec["taxes"][0]["charge_type"] == "On Net Total"
    assert spec["taxes"][0]["included_in_print_rate"] == 0


def test_case_b_is_ten_and_a_half_percent():
    spec = _especificaciones()["B"]

    assert spec["items"][0]["rate"] == 2500
    assert spec["taxes"][0]["rate"] == 10.5
    assert spec["taxes"][0]["account_head"] == "IVA 10.5"


def test_case_c_has_the_two_lines_that_make_the_tax_row_round():
    spec = _especificaciones()["C"]

    assert [(linea["qty"], linea["rate"]) for linea in spec["items"]] == [
        (3, 333.33),
        (2, 500),
    ]


def test_case_d_uses_the_zero_percent_account():
    spec = _especificaciones()["D"]

    assert spec["taxes"][0]["rate"] == 0
    assert spec["taxes"][0]["account_head"] == "IVA 0"


def test_case_e_carries_a_global_discount():
    spec = _especificaciones()["E"]

    assert spec["discount_amount"] == 100
    assert spec["apply_discount_on"] == "Grand Total"


def test_case_f_carries_a_line_discount_on_top_of_the_list_price():
    spec = _especificaciones()["F"]

    assert spec["items"][0]["price_list_rate"] == 1000
    assert spec["items"][0]["discount_percentage"] == 10


def test_case_g_has_two_iva_rows():
    spec = _especificaciones()["G"]

    assert [fila["rate"] for fila in spec["taxes"]] == [21, 10.5]


class _FrappeCuentas:
    """Enough of ``frappe`` for ``asegurar_cuenta``."""

    def __init__(self, existentes=(), padres=("Padre - TST",)):
        self.existentes = list(existentes)
        self.padres = list(padres)
        self.creadas = []

    def get_all(self, doctype, filters=None, pluck=None, limit=None):
        if filters.get("is_group") == 1:
            return self.padres
        return self.existentes

    def get_doc(self, datos):
        doc = _Doc(datos)
        doc.name = f"{datos['account_name']} - TST"
        self.creadas.append(doc)
        return doc


def test_asegurar_cuenta_reuses_an_existing_account():
    frappe = _FrappeCuentas(existentes=["Ventas Gravadas - TST"])
    preparar = _cargar("preparar", PREPARAR, frappe)

    nombre = preparar.asegurar_cuenta("Demo SA", "Ventas Gravadas", "Income")

    assert nombre == "Ventas Gravadas - TST"
    assert frappe.creadas == []


def test_asegurar_cuenta_creates_a_missing_one_under_a_group_of_its_root_type():
    frappe = _FrappeCuentas()
    preparar = _cargar("preparar", PREPARAR, frappe)

    nombre = preparar.asegurar_cuenta("Demo SA", "IVA 21", "Liability")

    assert nombre == "IVA 21 - TST"
    assert frappe.creadas[0]["parent_account"] == "Padre - TST"
    assert frappe.creadas[0]["company"] == "Demo SA"
