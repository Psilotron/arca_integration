"""The Frappe layer resolves ARCA profiles by company and environment.

The module cannot run here — there is no bench — so ``frappe`` is replaced by a
stub that answers the three calls ``api.py`` makes: ``db.get_value``,
``get_doc`` and ``get_site_path``. What is verified is the decision, not Frappe.
"""

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from arca_integration.core.errors import AuthError, WsfeError
from arca_integration.core.endpoints import endpoints_para_ambiente

API = Path(__file__).parents[1] / "arca_integration" / "api.py"


class _FakeFrappe(types.ModuleType):
    def __init__(self, configs):
        super().__init__("frappe")
        self.configs = list(configs)
        self.db = types.SimpleNamespace(get_value=self._get_value)
        self.utils = types.ModuleType("frappe.utils")
        self.utils.now_datetime = lambda: "2026-11-13 00:00:00"
        self.exceptions = types.ModuleType("frappe.exceptions")
        self.exceptions.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
        self.site = "/site"

    def _get_value(self, doctype, filters, field):
        assert doctype == "ARCA Settings"
        doc = next(
            (
                config
                for config in self.configs
                if all(config.get(key) == value for key, value in filters.items())
            ),
            None,
        )
        return doc.get(field) if doc else None

    def get_doc(self, doctype, name=None, *args, **kwargs):
        if doctype == "ARCA Settings":
            for doc in self.configs:
                if doc.get("name") == name:
                    return doc
            raise AssertionError(f"unknown settings document: {name}")
        raise AssertionError(f"unexpected get_doc({doctype}, {name})")

    def get_site_path(self, *parts):
        return "/".join([self.site, *parts])

    def throw(self, message):
        raise RuntimeError(message)

    def whitelist(self):
        return lambda function: function


def _load(monkeypatch, configs):
    frappe = _FakeFrappe(configs)
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.utils", frappe.utils)
    monkeypatch.setitem(sys.modules, "frappe.exceptions", frappe.exceptions)

    spec = importlib.util.spec_from_file_location("arca_api_under_test", API)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, frappe


CONFIG = {
    "name": "ACME",
    "company": "ACME",
    "environment": "Homologación",
    "is_default": 1,
    "cuit_representada": "20000000001",
}

EXPECTED_ENDPOINTS = {
    "Homologación": {
        "wsaa_url": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        "padron_url": (
            "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5"
        ),
        "wsfe_url": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
    "Producción": {
        "wsaa_url": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
        "padron_url": (
            "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5"
        ),
        "wsfe_url": "https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
}


def test_default_settings_are_resolved_by_company(monkeypatch):
    module, _ = _load(monkeypatch, [CONFIG])

    settings = module._settings("ACME")

    assert settings["cuit_representada"] == "20000000001"


def test_settings_are_resolved_by_company_and_environment(monkeypatch):
    production = {
        **CONFIG,
        "name": "ACME-PROD",
        "environment": "Producción",
        "is_default": 0,
    }
    module, _ = _load(monkeypatch, [CONFIG, production])

    assert module._settings("ACME", "Producción") is production
    assert module._settings("ACME") is CONFIG


def test_an_explicit_empty_environment_does_not_fall_back_to_the_default(monkeypatch):
    module, _ = _load(monkeypatch, [CONFIG])

    with pytest.raises(AuthError, match="sin definir"):
        module._settings("ACME", "")


def test_a_company_without_configuration_is_reported(monkeypatch):
    module, _ = _load(monkeypatch, [])

    with pytest.raises(AuthError) as excinfo:
        module._settings("ACME")

    assert "configuración ARCA" in excinfo.value.message


def test_the_company_is_required(monkeypatch):
    module, _ = _load(monkeypatch, [])

    with pytest.raises(AuthError) as excinfo:
        module._settings(None)

    assert "empresa" in excinfo.value.message.lower()


def test_ticket_cache_is_isolated_by_company_and_environment(monkeypatch):
    module, _ = _load(monkeypatch, [CONFIG])

    homologacion = module._ticket_cache("ACME", "Homologación")
    homologacion_otra = module._ticket_cache("Otra Empresa", "Homologación")
    produccion = module._ticket_cache("ACME", "Producción")

    assert homologacion.path == module._ticket_cache("ACME", "Homologación").path
    assert homologacion.path != homologacion_otra.path
    assert homologacion.path != produccion.path
    assert "ACME" not in str(homologacion.path)


@pytest.mark.parametrize("environment", ["Homologación", "Producción"])
def test_clients_resolve_urls_from_environment_not_saved_fields(
    monkeypatch, environment
):
    module, _ = _load(monkeypatch, [])
    resolved = {}
    settings = types.SimpleNamespace(
        environment=environment,
        wsaa_url="https://legacy.example/wsaa",
        padron_url="https://legacy.example/padron",
        wsfe_url="https://legacy.example/wsfe",
        certificate="private/files/arca.crt",
        cuit_representada="20000000001",
    )
    module._site_file_path = lambda file_url: "/tmp/arca.crt"
    module._ticket_cache = lambda company, selected_environment: (
        company,
        selected_environment,
    )
    module.ArcaClient = lambda **kwargs: resolved.setdefault("padron", kwargs)
    module.WsfeClient = lambda **kwargs: resolved.setdefault("wsfe", kwargs)

    module._client(settings, "/tmp/arca.key", "ACME")
    module._wsfe_client(settings, "/tmp/arca.key", "ACME")

    endpoints = endpoints_para_ambiente(environment)
    assert endpoints == EXPECTED_ENDPOINTS[environment]
    assert resolved["padron"]["wsaa_url"] == endpoints["wsaa_url"]
    assert resolved["padron"]["padron_url"] == endpoints["padron_url"]
    assert resolved["wsfe"]["wsaa_url"] == endpoints["wsaa_url"]
    assert resolved["wsfe"]["wsfe_url"] == endpoints["wsfe_url"]


def _stub_the_network(monkeypatch, module, lookup):
    """Replace everything that needs a bench, keeping ``consultar`` itself real."""
    module._settings = lambda company, environment: CONFIG
    module._private_key_file = lambda settings: contextlib.nullcontext("/tmp/erp.key")
    module._check_company_access = lambda company: company
    module._target_document = lambda cuit, company, environment, **_kwargs: (
        types.SimpleNamespace(
            name="QUERY-1",
            environment=environment,
            queried_at="2026-11-13 00:00:00",
        )
    )
    module._client = lambda settings, key_path, company: types.SimpleNamespace(
        lookup=lookup
    )
    module._record = lambda *args, **kwargs: kwargs["doc"]


def test_consultar_returns_exactly_the_keys_the_form_reads(monkeypatch):
    module, _ = _load(monkeypatch, [CONFIG])
    persona = types.SimpleNamespace(
        razon_social="ACME SA",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw="<xml/>",
    )
    _stub_the_network(monkeypatch, module, lambda cuit: persona)

    result = module.consultar(
        "20-00000000-1", company="ACME", environment="Homologación"
    )

    assert set(result) == {
        "status",
        "name",
        "razon_social",
        "tipo_persona",
        "estado_clave",
        "error",
        "cuit",
        "company",
        "environment",
        "queried_at",
    }
    assert result["status"] == "OK"
    assert result["company"] == "ACME"
    assert result["environment"] == "Homologación"
    assert result["cuit"] == "20000000001"
    assert result["queried_at"] == "2026-11-13 00:00:00"
    assert result["error"] is None


def test_consultar_reports_a_lookup_failure_with_the_same_keys(monkeypatch):
    module, _ = _load(monkeypatch, [CONFIG])

    def falla(cuit):
        raise AuthError("boom")

    _stub_the_network(monkeypatch, module, falla)

    result = module.consultar(
        "20-00000000-1", company="ACME", environment="Homologación"
    )

    assert set(result) == {
        "status",
        "name",
        "razon_social",
        "tipo_persona",
        "estado_clave",
        "error",
        "cuit",
        "company",
        "environment",
        "queried_at",
    }
    assert result["status"] == "Error"
    assert result["error"] == "boom"
    assert result["environment"] == "Homologación"


@pytest.mark.parametrize("environment", ["Homologación", "Producción"])
def test_consultar_uses_the_selected_company_environment(monkeypatch, environment):
    production = {
        **CONFIG,
        "name": "ACME-PROD",
        "environment": "Producción",
        "is_default": 0,
    }
    module, _ = _load(monkeypatch, [CONFIG, production])
    selected_settings = []
    persona = types.SimpleNamespace(
        razon_social="ACME SA",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw="<xml/>",
    )
    module._check_company_access = lambda company: company
    module._target_document = lambda cuit, company, selected_environment, **_kwargs: (
        types.SimpleNamespace(
            name="QUERY-1",
            environment=selected_environment,
            queried_at="2026-11-13 00:00:00",
        )
    )
    module._private_key_file = lambda settings: contextlib.nullcontext("/tmp/erp.key")
    module._client = lambda settings, key_path, company: (
        selected_settings.append(settings)
        or types.SimpleNamespace(lookup=lambda cuit: persona)
    )
    module._record = lambda *args, **kwargs: kwargs["doc"]

    module.consultar("20-00000000-1", company="ACME", environment=environment)

    expected = CONFIG if environment == "Homologación" else production
    assert selected_settings == [expected]


def test_consultar_records_the_environment_when_its_profile_is_missing(monkeypatch):
    module, frappe = _load(monkeypatch, [CONFIG])
    document = types.SimpleNamespace(
        name="QUERY-1",
        company="ACME",
        razon_social="Resultado anterior",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw_response="<old/>",
        save=lambda: None,
        check_permission=lambda permission: None,
    )
    frappe.has_permission = lambda *args, **kwargs: True
    frappe.new_doc = lambda doctype: document
    module._check_company_access = lambda company: company

    def no_network(*args):
        raise AssertionError("a missing profile must not call ARCA")

    module._client = no_network

    result = module.consultar(
        "20-00000000-1",
        name=document.name,
        company="ACME",
        environment="Producción",
    )

    assert result["status"] == "Error"
    assert "Producción" in result["error"]
    assert document.environment == "Producción"
    assert document.status == "Error"
    assert document.error_message == result["error"]
    assert document.razon_social is None
    assert document.tipo_persona is None
    assert document.estado_clave is None
    assert document.raw_response is None


def _stub_point_catalog(module, frappe, settings, lookup):
    permission_checks = []
    settings.check_permission = lambda permission: permission_checks.append(permission)
    requested = {}

    def get_doc(doctype, name):
        assert doctype == "ARCA Settings"
        requested["name"] = name
        return settings

    frappe.get_doc = get_doc
    module._check_company_access = lambda company: company
    module._private_key_file = lambda _profile: contextlib.nullcontext("/tmp/erp.key")
    module._wsfe_client = lambda _profile, _key_path, _company: types.SimpleNamespace(
        ptos_venta=lookup
    )
    return permission_checks, requested


def test_point_catalog_uses_the_named_profile_and_returns_status_metadata(monkeypatch):
    module, frappe = _load(monkeypatch, [])
    settings = types.SimpleNamespace(
        name="ACME-PROD",
        company="ACME",
        environment="Producción",
        pto_venta=7,
    )
    permission_checks, requested = _stub_point_catalog(
        module,
        frappe,
        settings,
        lambda: [
            types.SimpleNamespace(
                nro=7,
                emision_tipo="CAE",
                bloqueado=False,
                fch_baja=None,
            ),
            types.SimpleNamespace(
                nro=8,
                emision_tipo="CAEA",
                bloqueado=True,
                fch_baja="20240101",
            ),
        ],
    )

    result = module.consultar_puntos_venta("ACME-PROD")

    assert requested["name"] == "ACME-PROD"
    assert permission_checks == ["read"]
    assert result == [
        {
            "nro": 7,
            "emision_tipo": "CAE",
            "bloqueado": False,
            "fch_baja": None,
        },
        {
            "nro": 8,
            "emision_tipo": "CAEA",
            "bloqueado": True,
            "fch_baja": "20240101",
        },
    ]
    assert settings.pto_venta == 7


def test_point_catalog_602_is_explicit_and_does_not_change_the_saved_value(
    monkeypatch,
):
    module, frappe = _load(monkeypatch, [])
    settings = types.SimpleNamespace(
        name="ACME-HOMO",
        company="ACME",
        environment="Homologación",
        pto_venta=1,
    )

    def fail():
        raise WsfeError(
            "Sin resultados.", code=602, detail="FEParamGetPtosVenta"
        )

    _stub_point_catalog(module, frappe, settings, fail)

    with pytest.raises(RuntimeError, match="no confirma que el punto configurado"):
        module.consultar_puntos_venta("ACME-HOMO")

    assert settings.pto_venta == 1
