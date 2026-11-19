"""The ``ARCA Comprobante`` controller, without a site.

The controller only holds the rules that need the stored document: one
comprobante per invoice, a document that started an attempt is frozen, and
evidence with a possible number is not deletable. Every fiscal rule stays in
``core/`` and is tested there.

``frappe`` is replaced by a stub that answers ``throw``, ``db.get_value`` and
provides ``Document``.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

CONTROLLER = (
    Path(__file__).parents[1]
    / "arca_integration"
    / "arca_integration"
    / "doctype"
    / "arca_comprobante"
    / "arca_comprobante.py"
)


class ErrorDeFormulario(Exception):
    """What ``frappe.throw`` raises: the message the operator reads."""


class _Document:
    def __init__(self, values=None):
        self.__dict__.update(values or {})

    def __getattr__(self, fieldname):
        if fieldname.startswith("__"):
            raise AttributeError(fieldname)
        return None

    def is_new(self):
        return self.__dict__.get("_nuevo", True)

    def get(self, fieldname, default=None):
        return getattr(self, fieldname, default)


def _load(monkeypatch, guardados=None):
    guardados = guardados or {}

    frappe = types.ModuleType("frappe")
    frappe.throw = lambda message, *args, **kwargs: (_ for _ in ()).throw(
        ErrorDeFormulario(message)
    )

    def get_value(doctype, filters, field=None, as_dict=False):
        if isinstance(filters, dict):
            return guardados.get(filters.get("sales_invoice"))
        return None

    frappe.db = types.SimpleNamespace(get_value=get_value)

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Document
    model.document = document
    frappe.model = model

    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.model", model)
    monkeypatch.setitem(sys.modules, "frappe.model.document", document)

    spec = importlib.util.spec_from_file_location("arca_comprobante_controller", CONTROLLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, frappe


def _comprobante(module, **values):
    datos = {
        "name": "ARCA-COMP-0",
        "sales_invoice": "ACC-SINV-2026-00001",
        "estado": "Borrador",
        "cbte_tipo": "6",
        "doc_tipo": "99",
        "doc_nro": "0",
        "intentos": 0,
        "_nuevo": True,
    }
    datos.update(values)
    return module.ARCAComprobante(datos)


# --- one comprobante per invoice --------------------------------------------


def test_a_second_comprobante_for_the_same_invoice_is_refused(monkeypatch):
    module, frappe = _load(monkeypatch, {"ACC-SINV-2026-00001": "ARCA-COMP-1"})
    doc = _comprobante(module)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        doc.before_insert()

    assert "ARCA-COMP-1" in str(excinfo.value)
    assert "ACC-SINV-2026-00001" in str(excinfo.value)


def test_the_first_comprobante_for_an_invoice_is_created(monkeypatch):
    module, frappe = _load(monkeypatch)
    doc = _comprobante(module)

    doc.before_insert()


# --- a started attempt is frozen --------------------------------------------


@pytest.mark.parametrize("estado", ["Solicitando", "Autorizado", "Incierto"])
def test_a_started_attempt_cannot_be_edited(monkeypatch, estado):
    module, frappe = _load(monkeypatch)
    doc = _comprobante(module, _nuevo=False, estado="Borrador", cbte_tipo="6")

    def get_value(doctype, filters, field=None, as_dict=False):
        return {"estado": estado, "sales_invoice": "ACC-SINV-2026-00001"}

    frappe.db.get_value = get_value

    with pytest.raises(ErrorDeFormulario) as excinfo:
        doc.validate()

    assert estado in str(excinfo.value)
    assert "Conciliar" in str(excinfo.value)


@pytest.mark.parametrize("estado", ["Borrador", "Rechazado"])
def test_a_comprobante_that_was_never_sent_stays_correctable(monkeypatch, estado):
    module, frappe = _load(monkeypatch)
    doc = _comprobante(module, _nuevo=False, estado=estado)

    def get_value(doctype, filters, field=None, as_dict=False):
        return {"estado": estado, "sales_invoice": "ACC-SINV-2026-00001"}

    frappe.db.get_value = get_value

    doc.validate()

    assert doc.cbte_clase == "B"


def test_the_invoice_of_a_comprobante_cannot_be_changed(monkeypatch):
    module, frappe = _load(monkeypatch)
    doc = _comprobante(module, _nuevo=False, sales_invoice="ACC-SINV-2026-00002")

    def get_value(doctype, filters, field=None, as_dict=False):
        return {"estado": "Borrador", "sales_invoice": "ACC-SINV-2026-00001"}

    frappe.db.get_value = get_value

    with pytest.raises(ErrorDeFormulario) as excinfo:
        doc.validate()

    assert "no se puede cambiar" in str(excinfo.value)


# --- the class follows the type ---------------------------------------------


def test_the_class_is_derived_from_the_confirmed_type(monkeypatch):
    module, _ = _load(monkeypatch)

    assert _comprobante(module, cbte_tipo="1").cbte_clase is None
    doc = _comprobante(module, cbte_tipo="1")
    doc.validate()
    assert doc.cbte_clase == "A"


def test_a_type_outside_the_measured_ones_is_refused(monkeypatch):
    module, _ = _load(monkeypatch)
    doc = _comprobante(module, cbte_tipo="3")

    with pytest.raises(ErrorDeFormulario) as excinfo:
        doc.validate()

    assert "Factura A" in str(excinfo.value)
    assert "Factura B" in str(excinfo.value)


def test_without_a_type_there_is_no_class(monkeypatch):
    module, _ = _load(monkeypatch)
    doc = _comprobante(module, cbte_tipo=None, cbte_clase="B")

    doc.validate()

    assert doc.cbte_clase is None


# --- the receiver's document ------------------------------------------------


def test_a_typed_document_without_number_can_be_saved_for_completion(monkeypatch):
    module, _ = _load(monkeypatch)
    doc = _comprobante(module, doc_tipo="80", doc_nro="")

    doc.validate()

    assert doc.doc_nro == ""


def test_an_unidentified_consumer_is_normalized_to_document_number_zero(monkeypatch):
    module, _ = _load(monkeypatch)
    doc = _comprobante(module, doc_tipo="99", doc_nro="20000000001")

    doc.validate()

    assert doc.doc_nro == "0"


def test_a_document_number_that_needs_correction_can_be_saved(monkeypatch):
    module, _ = _load(monkeypatch)
    doc = _comprobante(module, doc_tipo="80", doc_nro="20-00000000-1")

    doc.validate()

    assert doc.doc_nro == "20-00000000-1"


# --- evidence is not deletable ----------------------------------------------


def test_a_comprobante_with_attempts_cannot_be_deleted(monkeypatch):
    module, _ = _load(monkeypatch)
    doc = _comprobante(module, intentos=1)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        doc.on_trash()

    assert "no se puede borrar" in str(excinfo.value)


def test_a_comprobante_that_was_never_sent_can_be_deleted(monkeypatch):
    module, _ = _load(monkeypatch)

    _comprobante(module, intentos=0).on_trash()
