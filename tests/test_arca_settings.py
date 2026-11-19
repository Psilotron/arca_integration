"""ARCA environment profiles, checked without a bench.

``ARCA Settings`` used to be a ``Single``: one certificate, one CUIT and one
point of sale for the whole site. It now stores one profile per company and
environment. What this module verifies is the schema and controller validation.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).parents[1]
SETTINGS = (
    ROOT
    / "arca_integration"
    / "arca_integration"
    / "doctype"
    / "arca_settings"
    / "arca_settings.json"
)
CONTROLLER = SETTINGS.with_suffix(".py")


def _document() -> dict:
    return json.loads(SETTINGS.read_text())


def _fields() -> dict:
    return {field["fieldname"]: field for field in _document()["fields"]}


def _controller(tax_id=None, profiles=()):
    """Load the controller with a stub ``frappe``, like the bench scripts."""
    frappe = types.ModuleType("frappe")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    frappe._dict = dict
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    profiles = list(profiles)

    def get_value(doctype, selector, field, **kwargs):
        if doctype == "Company":
            return tax_id if field == "tax_id" else selector
        if doctype == "ARCA Settings":
            profile = next(
                (
                    row
                    for row in profiles
                    if (
                        all(getattr(row, key, None) == value for key, value in selector.items())
                        if isinstance(selector, dict)
                        else getattr(row, "name", None) == selector
                    )
                ),
                None,
            )
            return getattr(profile, field, None) if profile else None
        raise AssertionError(f"unexpected get_value({doctype}, {selector}, {field})")

    def set_value(doctype, name, field, value, **kwargs):
        profile = next(
            (row for row in profiles if row.name == name),
            None,
        )
        if profile:
            setattr(profile, field, value)

    def get_all(doctype, filters, pluck, limit):
        assert doctype == "ARCA Settings"
        return [
            getattr(row, pluck)
            for row in profiles
            if all(
                getattr(row, field, None) != value[1]
                if isinstance(value, list) and value[0] == "!="
                else getattr(row, field, None) == value
                for field, value in filters.items()
            )
        ][:limit]

    frappe.db = types.SimpleNamespace(get_value=get_value, set_value=set_value)
    frappe.get_all = get_all

    def throw(message, *args, **kwargs):
        raise frappe.ValidationError(message)

    frappe.throw = throw

    previous = {name: sys.modules.get(name) for name in ("frappe", "frappe.model", "frappe.model.document")}
    sys.modules["frappe"] = frappe
    sys.modules["frappe.model"] = types.ModuleType("frappe.model")
    sys.modules["frappe.model.document"] = document
    try:
        spec = importlib.util.spec_from_file_location("arca_settings_controller", CONTROLLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def test_settings_is_one_document_per_company():
    doc = _document()

    assert doc.get("issingle", 0) == 0
    assert doc["autoname"] == "hash"
    assert _fields()["company"]["fieldtype"] == "Link"
    assert _fields()["company"]["options"] == "Company"


def test_every_settings_document_names_its_company():
    field = _fields()["company"]

    assert field["reqd"] == 1
    assert not field.get("unique")
    assert field["in_list_view"] == 1
    assert _fields()["environment"]["in_list_view"] == 1
    assert _fields()["is_default"]["in_list_view"] == 1


def test_the_issuer_cuit_is_read_from_the_company_not_typed():
    field = _fields()["cuit_representada"]

    assert field["read_only"] == 1
    assert not field.get("reqd")
    assert not field.get("default")


def test_no_field_carries_an_arca_default():
    """An install must not be operational before someone configures it."""
    fields = _fields()

    for fieldname in (
        "environment",
        "pto_venta",
        "cbte_tipo",
    ):
        assert "default" not in fields[fieldname], fieldname


def test_the_environment_must_be_chosen_explicitly():
    field = _fields()["environment"]

    assert field["options"] == "Homologación\nProducción"
    assert field["reqd"] == 1
    assert "default" not in field


def test_the_lookup_and_wsfe_fields_survived_the_conversion():
    fields = _fields()

    for fieldname in (
        "company",
        "environment",
        "certificate",
        "private_key",
        "cuit_representada",
        "pto_venta",
        "cbte_tipo",
        "is_default",
    ):
        assert fieldname in fields
        assert fieldname in _document()["field_order"]


def test_the_private_key_stays_a_password():
    fields = _fields()

    assert fields["private_key"]["fieldtype"] == "Password"
    assert fields["private_key"]["hidden"] == 1


def test_no_arca_host_is_shipped_in_the_schema():
    serialized = SETTINGS.read_text()

    for marker in ("wsaahomo", "awshomo", "wswhomo", "afip.gov.ar"):
        assert marker not in serialized


def test_endpoints_are_resolved_from_the_environment():
    module = _controller()

    homologacion = module.endpoints_para_ambiente("Homologación")
    produccion = module.endpoints_para_ambiente("Producción")

    assert homologacion["wsaa_url"] == (
        "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
    )
    assert homologacion["wsfe_url"].startswith("https://wswhomo.")
    assert produccion["wsaa_url"] == "https://wsaa.afip.gov.ar/ws/services/LoginCms"
    assert produccion["wsfe_url"].startswith("https://servicios1.")


def test_an_unknown_environment_is_rejected():
    module = _controller()

    with pytest.raises(module.UnknownArcaEnvironment):
        module.endpoints_para_ambiente("")


def test_duplicate_company_environment_profile_is_rejected():
    profile = types.SimpleNamespace(
        name="ACME-HOMO",
        company="ACME",
        environment="Homologación",
        is_default=1,
    )
    module = _controller(tax_id="20-00000000-1", profiles=[profile])
    document = _documento(name="new-profile")

    with pytest.raises(module.frappe.ValidationError):
        module.ARCASettings.validate(document)


def test_second_environment_keeps_the_existing_profile_as_default():
    homologacion = types.SimpleNamespace(
        name="ACME-HOMO",
        company="ACME",
        environment="Homologación",
        is_default=1,
    )
    module = _controller(tax_id="20-00000000-1", profiles=[homologacion])
    produccion = _documento(
        name="ACME-PROD",
        environment="Producción",
        is_default=0,
    )

    module.ARCASettings.validate(produccion)

    assert homologacion.is_default == 1
    assert produccion.is_default == 0


def test_selecting_a_new_default_unsets_the_previous_profile():
    homologacion = types.SimpleNamespace(
        name="ACME-HOMO",
        company="ACME",
        environment="Homologación",
        is_default=1,
    )
    module = _controller(tax_id="20-00000000-1", profiles=[homologacion])
    produccion = _documento(
        name="ACME-PROD",
        environment="Producción",
        is_default=1,
    )

    module.ARCASettings.validate(produccion)

    assert homologacion.is_default == 0
    assert produccion.is_default == 1


def test_a_default_profile_cannot_be_deleted_while_another_profile_exists():
    homologacion = types.SimpleNamespace(
        name="ACME-HOMO",
        company="ACME",
        environment="Homologación",
        is_default=1,
    )
    produccion = types.SimpleNamespace(
        name="ACME-PROD",
        company="ACME",
        environment="Producción",
        is_default=0,
    )
    module = _controller(profiles=[homologacion, produccion])
    document = _Documento(
        name=homologacion.name,
        company=homologacion.company,
        is_default=homologacion.is_default,
    )

    with pytest.raises(module.frappe.ValidationError):
        module.ARCASettings.on_trash(document)


def test_the_only_default_profile_can_be_deleted():
    module = _controller()
    document = _Documento(
        name="ACME-HOMO",
        company="ACME",
        is_default=1,
    )

    module.ARCASettings.on_trash(document)


def test_suggested_voucher_type_only_accepts_supported_types():
    module = _controller(tax_id="20-00000000-1")
    document = _documento(cbte_tipo="3")

    with pytest.raises(module.frappe.ValidationError):
        module.ARCASettings.validate(document)


def test_the_issuer_cuit_comes_from_the_company_tax_id():
    module = _controller(tax_id="20-00000000-1")

    assert module.get_company_cuit("ACME") == "20000000001"


def test_a_company_without_tax_id_is_rejected():
    module = _controller(tax_id=None)

    with pytest.raises(module.ConfigurationError):
        module.get_company_cuit("ACME")


def test_an_invalid_tax_id_is_rejected():
    module = _controller(tax_id="no es un cuit")

    with pytest.raises(module.ConfigurationError):
        module.get_company_cuit("ACME")


def _documento(**cambios) -> "_Documento":
    """A document with every field ``validate()`` touches."""
    campos = dict(
        company="ACME",
        environment="Homologación",
        wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        padron_url="https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
        wsfe_url="https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
        certificate=None,
        private_key=None,
        private_key_paste=None,
    )
    campos.update(cambios)
    return _Documento(**campos)


class _Documento:
    """Enough of ``frappe.Document`` for ``validate()``: attributes plus get/set."""

    def __init__(self, **campos):
        self.__dict__.update(campos)

    def get(self, campo, default=None):
        return self.__dict__.get(campo, default)

    def set(self, campo, valor):
        self.__dict__[campo] = valor


def _clave_pem() -> str:
    """A synthetic private key. It never leaves the test."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_saving_fills_the_cuit_from_the_company():
    module = _controller(tax_id="20-00000000-1")
    document = _documento()

    module.ARCASettings.validate(document)

    assert document.cuit_representada == "20000000001"


def test_the_private_key_is_pasted_through_a_multiline_field():
    fields = _fields()
    paste = fields["private_key_paste"]

    # `Text` renders a <textarea> and maps to a `text` column: a PEM is ~1.7 KB.
    assert paste["fieldtype"] == "Text"
    assert not paste.get("hidden")
    assert paste.get("no_copy") == 1
    assert not paste.get("reqd")
    assert not paste.get("default")
    assert fields["private_key"]["fieldtype"] == "Password"
    assert "private_key_paste" in _document()["field_order"]


def test_the_pasted_key_moves_into_the_password_field():
    module = _controller(tax_id="20-00000000-1")
    pem = _clave_pem()
    document = _documento(private_key_paste=pem)

    module.ARCASettings.validate(document)

    assert document.private_key == pem
    assert document.private_key_paste is None


def test_the_pasted_key_keeps_its_line_breaks():
    module = _controller(tax_id="20-00000000-1")
    pem = _clave_pem()
    document = _documento(private_key_paste=pem.replace("\n", "\r\n"))

    module.ARCASettings.validate(document)

    assert document.private_key == pem
    assert document.private_key.count("\n") > 1


def test_an_empty_paste_leaves_the_stored_key_alone():
    module = _controller(tax_id="20-00000000-1")
    mask = "*" * 40
    document = _documento(private_key=mask, private_key_paste="   \n")

    module.ARCASettings.validate(document)

    # Frappe stores the mask in the column; replacing it would delete the key.
    assert document.private_key == mask
    assert document.private_key_paste is None


def test_a_value_that_is_not_a_private_key_is_refused():
    module = _controller(tax_id="20-00000000-1")
    document = _documento(private_key_paste="esto no es una clave")

    with pytest.raises(module.frappe.ValidationError):
        module.ARCASettings.validate(document)

    assert document.private_key is None


def test_saving_a_certificate_and_key_checks_that_the_pair_matches():
    module = _controller(tax_id="20-00000000-1")
    module.get_certificate_path = lambda certificate: "/tmp/arca.crt"
    module.validate_certificate_cuit = lambda cert, cuit: cuit
    checked = []
    module.validate_key_pair = lambda cert, key: checked.append((cert, key))
    pem = _clave_pem()
    document = _documento(certificate="private/files/arca.crt", private_key_paste=pem)

    module.ARCASettings.validate(document)

    assert checked == [("/tmp/arca.crt", pem.encode("utf-8"))]
    assert document.private_key_paste is None
