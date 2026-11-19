"""The emission endpoints, exercised with a stub ``frappe``.

There is no bench here, so the three calls ``api.py`` makes are faked:
``get_doc``, ``db.*`` and ``get_all``. What is verified is the decision the
endpoint takes — which state is written, what the operator reads, and which
mistakes are refused before a number is consumed — never Frappe itself.

``fiscal.emitir`` and ``fiscal.reconciliar`` run for real: the fake is the WSFE
client, so the number, the request and the classification are the production
code.
"""

import contextlib
import json
import types
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from arca_integration.core.comprobante import Estado
from arca_integration.core.errors import (
    AuthError,
    PersonaNotFound,
    TransportError,
    WsfeRejected,
    WsfeUncertain,
)
from arca_integration.core.wsfe.types import Cae

API = Path(__file__).parents[1] / "arca_integration" / "api.py"
FIXTURES = Path(__file__).parent / "fixtures" / "sales_invoice"
DOCTYPE_ROOT = (
    Path(__file__).parents[1] / "arca_integration" / "arca_integration" / "doctype"
)

EMPRESA = "Demo SA"
CAE = "75123456789012"


class ErrorDeFormulario(Exception):
    """What ``frappe.throw`` raises: the message the operator sees."""


class DuplicateEntryError(Exception):
    """What ``frappe.exceptions.DuplicateEntryError`` raises on a taken name."""


class _Doc:
    def __init__(self, doctype, values, store=None):
        self.doctype = doctype
        self.estado = None
        self.intentos = 0
        self._store = store
        self._frappe = None
        self.__dict__.update(values)
        self.veces_guardado = 0

    def __getattr__(self, fieldname):
        """Frappe documents answer ``None`` for a field that was never set."""
        if fieldname.startswith("__"):
            raise AttributeError(fieldname)
        return None

    def get(self, fieldname, default=None):
        return getattr(self, fieldname, default)

    def set(self, fieldname, value):
        setattr(self, fieldname, value)

    def append(self, fieldname, row):
        filas = list(getattr(self, fieldname, None) or [])
        filas.append(types.SimpleNamespace(**row))
        setattr(self, fieldname, filas)

    def insert(self, ignore_permissions=False):
        """Only the emission lock is inserted from a dict; its name is its key."""
        filas = self._store.setdefault(self.doctype, [])
        if any(fila.name == self.name for fila in filas):
            raise DuplicateEntryError(f"{self.doctype} {self.name} already exists")
        filas.append(self)
        return self

    def save(self, **kwargs):
        if kwargs.get("ignore_permissions"):
            raise AssertionError("document writes must not bypass permissions")
        self.veces_guardado += 1
        if self._store is not None:
            filas = self._store.setdefault(self.doctype, [])
            if self not in filas:
                filas.append(self)
        return self

    def check_permission(self, permtype):
        assert permtype in ("read", "write", "create"), permtype
        if self._frappe:
            self._frappe.permission_checks.append((self.doctype, permtype))
            if (self.doctype, permtype) in self._frappe.denied_permissions:
                raise ErrorDeFormulario(
                    f"permission denied: {self.doctype} {permtype}"
                )


class _FakeDb:
    def __init__(self, store, vivos=()):
        self.store = store
        self.vivos = list(vivos)
        self.writes = []

    def _doc(self, doctype, selector):
        for doc in self.store.get(doctype, []):
            if isinstance(selector, dict):
                if all(getattr(doc, field, None) == valor for field, valor in selector.items()):
                    return doc
            elif doc.name == selector:
                return doc
        return None

    def get_value(self, doctype, filters, field=None, as_dict=False):
        doc = self._doc(doctype, filters)
        if doc is None:
            return None
        if isinstance(field, (list, tuple)):
            valores = {name: getattr(doc, name, None) for name in field}
            return valores if as_dict else [valores[name] for name in field]
        return getattr(doc, field, None)

    def set_value(self, doctype, name, values, **kwargs):
        self.writes.append((doctype, name, dict(values)))
        doc = self._doc(doctype, name)
        if doc is not None:
            for fieldname, value in values.items():
                setattr(doc, fieldname, value)

    def sql(self, query, values, as_dict=False):
        if "update `tabARCA Bloqueo de Emision`" in query:
            return self._reclamar_bloqueo(values)
        if "delete from `tabARCA Bloqueo de Emision`" in query:
            return self._soltar_bloqueo(values)
        return self.vivos

    def _reclamar_bloqueo(self, values):
        """Only the expired lock is rewritten, exactly like the conditional UPDATE."""
        token, vence, detalle, clave, ahora = values
        for bloqueo in self.store.get("ARCA Bloqueo de Emision", []):
            if bloqueo.clave == clave and _momento(bloqueo.vence) < ahora:
                bloqueo.token = token
                bloqueo.vence = vence
                bloqueo.detalle = detalle
        return []

    def _soltar_bloqueo(self, values):
        clave, token = values
        self.store["ARCA Bloqueo de Emision"] = [
            bloqueo
            for bloqueo in self.store.get("ARCA Bloqueo de Emision", [])
            if not (bloqueo.clave == clave and bloqueo.token == token)
        ]
        return []

    def commit(self):
        self.commits = getattr(self, "commits", 0) + 1


def _defaults(doctype: str) -> dict:
    """The schema's own defaults, which Frappe applies when it creates a document.

    Reading them from the JSON keeps the stub honest: a missing or misspelled
    ``default`` in the DocType fails here, not on a site.
    """
    slug = doctype.lower().replace(" ", "_")
    path = DOCTYPE_ROOT / slug / f"{slug}.json"
    if not path.is_file():
        return {}

    return {
        field["fieldname"]: field["default"]
        for field in json.loads(path.read_text())["fields"]
        if field.get("default") not in (None, "")
    }


def _momento(valor):
    """A stored ``vence`` is a datetime on a site and text in a fixture."""
    if isinstance(valor, str):
        return datetime.fromisoformat(valor)
    return valor


class _FakeFrappe(types.ModuleType):
    def __init__(self, store, vivos=()):
        super().__init__("frappe")
        self.store = store
        self.db = _FakeDb(store, vivos)
        self.utils = types.ModuleType("frappe.utils")
        self.utils.now_datetime = lambda: datetime(2026, 11, 15, 10, 0, 0)
        self.exceptions = types.ModuleType("frappe.exceptions")
        self.exceptions.DuplicateEntryError = DuplicateEntryError
        self.usados = 0
        self.denied_permissions = set()
        self.permission_checks = []

    def get_doc(self, doctype, name=None, *args, **kwargs):
        if isinstance(doctype, dict):
            valores = dict(doctype)
            doctype = valores.pop("doctype")
            # ``autoname: field:clave``: the key is the document's name.
            valores["name"] = valores.get("clave")
            doc = _Doc(doctype, valores, self.store)
            doc._frappe = self
            return doc

        doc = self.db._doc(doctype, name)
        if doc is None:
            raise AssertionError(f"unknown {doctype}: {name}")
        doc._frappe = self
        return doc

    def new_doc(self, doctype):
        self.usados += 1
        valores = {"name": f"ARCA-COMP-{self.usados}", **_defaults(doctype)}
        doc = _Doc(doctype, valores, self.store)
        doc._frappe = self
        return doc

    def has_permission(self, doctype, ptype="read", doc=None, throw=False):
        denied = (doctype, ptype) in self.denied_permissions
        if denied and throw:
            raise ErrorDeFormulario(f"permission denied: {doctype} {ptype}")
        return not denied

    def get_all(self, doctype, filters=None, fields=None, **kwargs):
        filas = self.store.get(doctype, [])
        if filters:
            filas = [
                fila
                for fila in filas
                if all(getattr(fila, campo, None) == valor for campo, valor in filters.items())
            ]
        return [{campo: getattr(fila, campo, None) for campo in fields} for fila in filas] if fields else filas

    def get_site_path(self, *parts):
        return "/".join(["/site", *parts])

    def whitelist(self):
        return lambda function: function

    def throw(self, message, *args, **kwargs):
        raise ErrorDeFormulario(message)


def _factura(**extra):
    datos = json.loads((FIXTURES / "caso_a.json").read_text())
    datos.update({"name": "ACC-SINV-2026-00001", "docstatus": 1, "customer": "Cliente SA", "tax_id": ""})
    datos.update(extra)
    return _Doc("Sales Invoice", datos)


def _comprobante(**extra):
    datos = {
        "name": "ARCA-COMP-0",
        "sales_invoice": "ACC-SINV-2026-00001",
        "company": EMPRESA,
        "customer": "Cliente SA",
        "estado": Estado.BORRADOR.value,
        "cbte_nro": 0,
        "environment": "Homologación",
        "fecha_comprobante": "2026-11-12",
        "pto_venta": 1,
        "cbte_tipo": "6",
        "cbte_clase": "B",
        "concepto": 1,
        "condicion_iva_receptor": "5",
        "doc_tipo": "99",
        "doc_nro": "0",
        "imp_neto": 1000.0,
        "imp_iva": 210.0,
        "imp_trib": 0.0,
        "imp_total": 1210.0,
        "alicuotas": [
            types.SimpleNamespace(alicuota=5, base_imp=1000.0, importe=210.0)
        ],
    }
    datos.update(extra)
    return _Doc("ARCA Comprobante", datos)


def _store(comprobantes=()):
    return {
        "Sales Invoice": [_factura()],
        "ARCA Comprobante": list(comprobantes),
        "ARCA Mapeo de Cuenta de Ingreso": [
            _Doc(
                "ARCA Mapeo de Cuenta de Ingreso",
                {"company": EMPRESA, "account": "Ventas Gravadas", "naturaleza": "Gravado", "activo": 1},
            )
        ],
        "ARCA Mapeo de Cuenta de Impuesto": [
            _Doc(
                "ARCA Mapeo de Cuenta de Impuesto",
                {"company": EMPRESA, "account": "IVA 21%", "naturaleza": "IVA", "activo": 1},
            )
        ],
        "Company": [
            _Doc("Company", {"name": EMPRESA, "default_currency": "ARS"}),
        ],
    }


CONFIG = types.SimpleNamespace(
    company=EMPRESA,
    environment="Homologación",
    wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    wsfe_url="https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    padron_url="https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
    certificate=None,
    cuit_representada="20000000001",
    pto_venta=1,
    cbte_tipo=6,
    condicion_iva_emisor=1,
)


class ClienteFalso:
    """A ``WsfeClient`` stand-in: only the two fiscal operations are answered."""

    def __init__(self, ultimo=0, respuesta=None, error=None, al_enviar=None, ticket_error=None):
        self.ultimo = ultimo
        self.respuesta = respuesta
        self.error = error
        self.solicitud = None
        self.consulta = None
        self.tickets = 0
        self.al_enviar = al_enviar
        self.ticket_error = ticket_error

    def ticket(self):
        """WSAA, warmed before the attempt is frozen so a failure stays pre-send."""
        self.tickets += 1
        if self.ticket_error:
            raise self.ticket_error
        return None

    def ultimo_autorizado(self, pto_venta, cbte_tipo):
        self.numeracion = (pto_venta, cbte_tipo)
        return self.ultimo

    def solicitar_cae(self, solicitud):
        self.solicitud = solicitud
        if self.al_enviar is not None:
            self.al_enviar()
        if self.error:
            raise self.error
        return self.respuesta

    def consultar_comprobante(self, pto_venta, cbte_tipo, cbte_nro):
        self.consulta = (pto_venta, cbte_tipo, cbte_nro)
        if self.error:
            raise self.error
        return self.respuesta


def _load(monkeypatch, store, cliente=None, vivos=(), config=CONFIG):
    import importlib.util
    import sys

    frappe = _FakeFrappe(store, vivos)
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.utils", frappe.utils)
    monkeypatch.setitem(sys.modules, "frappe.exceptions", frappe.exceptions)

    spec = importlib.util.spec_from_file_location("arca_api_comprobante", API)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._settings = lambda company, environment=None: config
    module._private_key_file = lambda settings: contextlib.nullcontext("/tmp/erca.key")

    if cliente is not None:

        def fabrica(settings, key_path, company, captura=None):
            if captura is not None:
                captura["solicitud"] = b"<FECAESolicitar/>"
                captura["respuesta"] = b"<Respuesta/>"
            return cliente

        monkeypatch.setattr(module, "_wsfe_client", fabrica)

    return module, frappe


# --- preparar: the proposal, never an emission -------------------------------


def test_preparing_proposes_the_snapshot_and_the_amounts(monkeypatch):
    module, frappe = _load(monkeypatch, _store())

    name = module.preparar_comprobante("ACC-SINV-2026-00001")

    doc = frappe.store["ARCA Comprobante"][0]
    assert name == doc.name
    assert doc.estado == "Borrador"
    assert doc.environment == "Homologación"
    assert doc.fecha_comprobante == "2026-11-12"
    assert doc.pto_venta == 1
    assert doc.cbte_tipo == 6
    assert doc.condicion_iva_receptor is None
    assert doc.doc_tipo == "99"
    assert doc.doc_nro == "0"
    assert doc.imp_total == 1210.0
    assert doc.imp_neto == 1000.0
    assert len(doc.alicuotas) == 1
    assert doc.alicuotas[0].alicuota == 5
    assert doc.alicuotas[0].porcentaje == 21.0
    assert "condición frente al IVA del receptor" in doc.problema


def test_preparing_keeps_voucher_level_pos_and_type_overrides(monkeypatch):
    doc = _comprobante(pto_venta=7, cbte_tipo="1")
    module, _ = _load(monkeypatch, _store([doc]))

    module.preparar_comprobante("ACC-SINV-2026-00001")

    assert doc.pto_venta == 7
    assert doc.cbte_tipo == "1"


def test_preparing_reports_an_invalid_invoice_posting_date(monkeypatch):
    store = _store()
    store["Sales Invoice"][0].posting_date = ""
    module, frappe = _load(monkeypatch, store)

    module.preparar_comprobante("ACC-SINV-2026-00001")

    doc = frappe.store["ARCA Comprobante"][0]
    assert doc.fecha_comprobante == ""
    assert "fecha de contabilización" in doc.problema


def test_preparing_requires_create_permission_on_the_comprobante(monkeypatch):
    module, frappe = _load(monkeypatch, _store())
    frappe.denied_permissions.add(("ARCA Comprobante", "create"))

    with pytest.raises(ErrorDeFormulario, match="permission denied"):
        module.preparar_comprobante("ACC-SINV-2026-00001")

    assert frappe.store["ARCA Comprobante"] == []


def test_preparing_requires_company_read_permission(monkeypatch):
    module, frappe = _load(monkeypatch, _store())
    frappe.denied_permissions.add(("Company", "read"))

    with pytest.raises(ErrorDeFormulario, match="permission denied"):
        module.preparar_comprobante("ACC-SINV-2026-00001")

    assert frappe.store["ARCA Comprobante"] == []


def test_preparing_an_existing_proposal_requires_write_permission(monkeypatch):
    doc = _comprobante()
    module, frappe = _load(monkeypatch, _store([doc]))
    frappe.denied_permissions.add(("ARCA Comprobante", "write"))

    with pytest.raises(ErrorDeFormulario, match="permission denied"):
        module.preparar_comprobante("ACC-SINV-2026-00001")

    assert doc.veces_guardado == 0


def test_a_draft_invoice_is_refused(monkeypatch):
    module, frappe = _load(monkeypatch, _store())
    frappe.store["Sales Invoice"][0].docstatus = 0

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.preparar_comprobante("ACC-SINV-2026-00001")

    assert "enviada" in str(excinfo.value)


@pytest.mark.parametrize("docstatus", [0, 2])
def test_emission_refuses_a_non_submitted_invoice_before_requesting_a_ticket(
    monkeypatch, docstatus
):
    store = _store([_comprobante()])
    store["Sales Invoice"][0].docstatus = docstatus
    cliente = ClienteFalso(respuesta=_aprobado())
    module, _ = _load(monkeypatch, store, cliente)

    with pytest.raises(ErrorDeFormulario, match="no está enviada"):
        module.emitir_comprobante("ARCA-COMP-0")

    doc = store["ARCA Comprobante"][0]
    assert (doc.estado, doc.intentos, doc.cbte_nro) == ("Borrador", 0, 0)
    assert cliente.tickets == 0
    assert cliente.solicitud is None


def test_a_company_without_configuration_is_told_what_to_create(monkeypatch):
    module, frappe = _load(monkeypatch, _store())

    def sin_configuracion(company, environment=None):
        from arca_integration.core.errors import AuthError

        raise AuthError(f"La empresa {company} no tiene configuración ARCA.")

    module._settings = sin_configuracion

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.preparar_comprobante("ACC-SINV-2026-00001")

    assert "ARCA Settings" in str(excinfo.value)


def test_a_missing_mapping_is_stored_so_the_proposal_can_be_opened(monkeypatch):
    store = _store()
    store["ARCA Mapeo de Cuenta de Ingreso"] = []
    module, frappe = _load(monkeypatch, store)

    module.preparar_comprobante("ACC-SINV-2026-00001")

    doc = frappe.store["ARCA Comprobante"][0]
    assert "Ventas Gravadas" in doc.problema
    assert "ARCA Mapeo de Cuenta de Ingreso" in doc.problema
    assert doc.alicuotas == []


def test_preparing_twice_reuses_the_same_document(monkeypatch):
    module, frappe = _load(monkeypatch, _store())

    primero = module.preparar_comprobante("ACC-SINV-2026-00001")
    segundo = module.preparar_comprobante("ACC-SINV-2026-00001")

    assert primero == segundo
    assert len(frappe.store["ARCA Comprobante"]) == 1


def test_an_authorized_comprobante_is_only_returned_never_refreshed(monkeypatch):
    autorizado = _comprobante(estado="Autorizado", cae=CAE)
    module, frappe = _load(monkeypatch, _store([autorizado]))

    name = module.preparar_comprobante("ACC-SINV-2026-00001")

    assert name == autorizado.name
    assert autorizado.veces_guardado == 0


def test_the_receiver_document_comes_from_the_customers_tax_id(monkeypatch):
    store = _store()
    store["Sales Invoice"][0].tax_id = "20-00000000-1"
    module, frappe = _load(monkeypatch, store)

    module.preparar_comprobante("ACC-SINV-2026-00001")

    doc = frappe.store["ARCA Comprobante"][0]
    assert doc.doc_tipo == "80"
    assert doc.doc_nro == "20000000001"


def test_preparing_from_an_invoice_refreshes_an_existing_receiver_document(monkeypatch):
    store = _store([_comprobante(doc_tipo="99", doc_nro="0")])
    store["Sales Invoice"][0].tax_id = "27-12345678-9"
    module, frappe = _load(monkeypatch, store)

    module.preparar_comprobante("ACC-SINV-2026-00001")

    doc = frappe.store["ARCA Comprobante"][0]
    assert doc.doc_tipo == "80"
    assert doc.doc_nro == "27123456789"


def test_an_invalid_customer_tax_id_still_opens_a_correctable_proposal(monkeypatch):
    store = _store()
    store["Sales Invoice"][0].tax_id = "12.345.678"
    module, frappe = _load(monkeypatch, store)

    name = module.preparar_comprobante("ACC-SINV-2026-00001")

    doc = frappe.store["ARCA Comprobante"][0]
    assert name == doc.name
    assert "no es un CUIT" in doc.problema
    assert "tipo de documento del receptor" in doc.problema


def test_consultar_checks_company_and_query_create_permissions_before_network(
    monkeypatch,
):
    module, frappe = _load(monkeypatch, _store())
    llamadas = []
    module._client = lambda *args: types.SimpleNamespace(
        lookup=lambda cuit: llamadas.append(cuit)
    )
    frappe.denied_permissions.add(("ARCA CUIT Query", "create"))

    with pytest.raises(ErrorDeFormulario, match="permission denied"):
        module.consultar(
            "20-00000000-1", company=EMPRESA, environment="Homologación"
        )

    assert llamadas == []
    assert frappe.store.get("ARCA CUIT Query", []) == []


def test_consultar_creates_history_without_mutating_the_existing_query(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "company": "Otra Empresa", "cuit": "20000000001"},
    )
    store = _store()
    store["ARCA CUIT Query"] = [query]
    module, frappe = _load(monkeypatch, store)
    llamadas = []
    persona = types.SimpleNamespace(
        razon_social="CLIENTE SA",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw="<persona/>",
    )
    module._client = lambda *args: types.SimpleNamespace(
        lookup=lambda cuit: llamadas.append(cuit) or persona
    )

    module.consultar(
        "20-00000000-1",
        name=query.name,
        company=EMPRESA,
        environment="Homologación",
    )

    assert llamadas == ["20000000001"]
    assert query.company == "Otra Empresa"
    assert len(frappe.store["ARCA CUIT Query"]) == 2
    assert frappe.store["ARCA CUIT Query"][1].company == EMPRESA


def test_padron_lookup_from_invoice_creates_linked_history_with_context(monkeypatch):
    store = _store()
    invoice = store["Sales Invoice"][0]
    invoice.tax_id = "20-00000000-1"
    customer = _Doc(
        "Customer",
        {"name": "Cliente SA", "tax_id": "20-00000000-1", "customer_name": "Cliente SA"},
    )
    store["Customer"] = [customer]
    module, frappe = _load(monkeypatch, store)
    persona = types.SimpleNamespace(
        razon_social="CLIENTE REGISTRADO SA",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw="<persona/>",
    )
    module._client = lambda *_args: types.SimpleNamespace(
        lookup=lambda cuit: persona
    )

    result = module.consultar(
        invoice.tax_id,
        company=EMPRESA,
        environment="Homologación",
        customer=customer.name,
        sales_invoice=invoice.name,
    )

    query = frappe.store["ARCA CUIT Query"][0]
    assert result["status"] == "OK"
    assert result["company"] == EMPRESA
    assert result["environment"] == "Homologación"
    assert result["queried_at"] == query.queried_at
    assert query.cuit == "20000000001"
    assert query.customer == customer.name
    assert query.sales_invoice == invoice.name
    assert query.raw_response == "<persona/>"


def test_padron_lookup_from_supplier_creates_linked_history(monkeypatch):
    store = _store()
    supplier = _Doc(
        "Supplier",
        {
            "name": "Proveedor SA",
            "tax_id": "30-12345678-9",
            "supplier_name": "Proveedor SA",
        },
    )
    store["Supplier"] = [supplier]
    module, frappe = _load(monkeypatch, store)
    persona = types.SimpleNamespace(
        razon_social="PROVEEDOR REGISTRADO SA",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw="<persona/>",
    )
    module._client = lambda *_args: types.SimpleNamespace(
        lookup=lambda cuit: persona
    )

    result = module.consultar(
        supplier.tax_id,
        company=EMPRESA,
        environment="Homologación",
        supplier=supplier.name,
    )

    query = frappe.store["ARCA CUIT Query"][0]
    assert result["status"] == "OK"
    assert query.cuit == "30123456789"
    assert query.supplier == supplier.name
    assert query.customer is None


def test_padron_lookup_refuses_a_supplier_cuit_mismatch_before_network(monkeypatch):
    store = _store()
    supplier = _Doc("Supplier", {"name": "Proveedor SA", "tax_id": "30123456789"})
    store["Supplier"] = [supplier]
    module, frappe = _load(monkeypatch, store)
    calls = []
    module._client = lambda *_args: types.SimpleNamespace(
        lookup=lambda cuit: calls.append(cuit)
    )

    with pytest.raises(ErrorDeFormulario, match="CUIT no coincide con el del proveedor"):
        module.consultar(
            "20-00000000-1",
            company=EMPRESA,
            environment="Homologación",
            supplier=supplier.name,
        )

    assert calls == []
    assert frappe.store.get("ARCA CUIT Query", []) == []


def test_padron_result_returns_the_generated_query_name_after_save(monkeypatch):
    store = _store()
    module, frappe = _load(monkeypatch, store)
    query = _Doc("ARCA CUIT Query", {}, store)

    def save_with_generated_name():
        query.name = "ARCA-QUERY-HASH"
        store.setdefault("ARCA CUIT Query", []).append(query)
        return query

    query.save = save_with_generated_name
    frappe.new_doc = lambda doctype: query
    persona = types.SimpleNamespace(
        razon_social="CLIENTE SA",
        tipo_persona="JURIDICA",
        estado_clave="ACTIVO",
        raw="<persona/>",
    )
    module._client = lambda *_args: types.SimpleNamespace(
        lookup=lambda cuit: persona
    )

    result = module.consultar(
        "20-00000000-1", company=EMPRESA, environment="Homologación"
    )

    assert result["name"] == query.name
    assert result["name"] == "ARCA-QUERY-HASH"


def test_padron_lookup_refuses_an_invoice_cuit_mismatch_before_network(monkeypatch):
    store = _store()
    invoice = store["Sales Invoice"][0]
    invoice.tax_id = "20123456789"
    module, frappe = _load(monkeypatch, store)
    calls = []
    module._client = lambda *_args: types.SimpleNamespace(
        lookup=lambda cuit: calls.append(cuit)
    )

    with pytest.raises(ErrorDeFormulario, match="CUIT no coincide"):
        module.consultar(
            "20-00000000-1",
            company=EMPRESA,
            environment="Homologación",
            sales_invoice=invoice.name,
        )

    assert calls == []
    assert frappe.store.get("ARCA CUIT Query", []) == []


def test_padron_no_encontrado_is_recorded_as_a_distinct_status(monkeypatch):
    store = _store()
    module, frappe = _load(monkeypatch, store)

    def no_encontrado(cuit):
        raise PersonaNotFound("ARCA no encontró una persona con ese CUIT.")

    module._client = lambda *_args: types.SimpleNamespace(lookup=no_encontrado)
    result = module.consultar(
        "20-00000000-1", company=EMPRESA, environment="Homologación"
    )

    query = frappe.store["ARCA CUIT Query"][0]
    assert result["status"] == "No encontrado"
    assert result["environment"] == "Homologación"
    assert query.status == "No encontrado"
    assert query.error_message == result["error"]


def test_padron_profile_list_only_includes_readable_companies(monkeypatch):
    store = _store()
    store["ARCA Settings"] = [
        _Doc(
            "ARCA Settings",
            {
                "name": "Demo-Homo",
                "company": EMPRESA,
                "environment": "Homologación",
                "is_default": 1,
            },
        ),
        _Doc(
            "ARCA Settings",
            {
                "name": "Other-Homo",
                "company": "Otra Empresa",
                "environment": "Homologación",
                "is_default": 1,
            },
        ),
    ]
    store["Company"].append(_Doc("Company", {"name": "Otra Empresa"}))
    module, frappe = _load(monkeypatch, store)
    frappe.has_permission = lambda doctype, ptype="read", doc=None, **_kwargs: (
        doctype != "Company" or doc.name == EMPRESA
    )

    result = module.perfiles_padron()

    assert result == [
        {
            "name": "Demo-Homo",
            "company": EMPRESA,
            "environment": "Homologación",
            "is_default": 1,
        }
    ]


def test_padron_query_can_be_linked_to_a_saved_customer(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "cuit": "20000000001", "customer": None},
    )
    customer = _Doc(
        "Customer",
        {"name": "Cliente SA", "tax_id": "20-00000000-1"},
    )
    store = _store()
    store["ARCA CUIT Query"] = [query]
    store["Customer"] = [customer]
    module, frappe = _load(monkeypatch, store)

    module.vincular_consulta_a_cliente(query.name, customer.name)

    assert query.customer == customer.name
    assert frappe.db.writes[-1] == (
        "ARCA CUIT Query",
        query.name,
        {"customer": customer.name},
    )


def test_padron_query_cannot_be_linked_to_a_customer_with_another_cuit(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "cuit": "20000000001", "customer": None},
    )
    customer = _Doc(
        "Customer",
        {"name": "Cliente SA", "tax_id": "20123456789"},
    )
    store = _store()
    store["ARCA CUIT Query"] = [query]
    store["Customer"] = [customer]
    module, frappe = _load(monkeypatch, store)

    with pytest.raises(ErrorDeFormulario, match="CUIT del cliente no coincide"):
        module.vincular_consulta_a_cliente(query.name, customer.name)

    assert query.customer is None
    assert frappe.db.writes == []


def test_padron_supplier_query_cannot_be_relinked_to_a_customer(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "cuit": "30123456789", "supplier": "Proveedor SA"},
    )
    customer = _Doc(
        "Customer",
        {"name": "Cliente SA", "tax_id": "30-12345678-9"},
    )
    store = _store()
    store["ARCA CUIT Query"] = [query]
    store["Customer"] = [customer]
    module, frappe = _load(monkeypatch, store)

    with pytest.raises(ErrorDeFormulario, match="vinculada a un proveedor"):
        module.vincular_consulta_a_cliente(query.name, customer.name)

    assert query.customer is None
    assert frappe.db.writes == []


def test_padron_query_can_be_linked_to_a_saved_supplier(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "cuit": "30123456789", "supplier": None},
    )
    supplier = _Doc(
        "Supplier",
        {"name": "Proveedor SA", "tax_id": "30-12345678-9"},
    )
    store = _store()
    store["ARCA CUIT Query"] = [query]
    store["Supplier"] = [supplier]
    module, frappe = _load(monkeypatch, store)

    module.vincular_consulta_a_proveedor(query.name, supplier.name)

    assert query.supplier == supplier.name
    assert frappe.db.writes[-1] == (
        "ARCA CUIT Query",
        query.name,
        {"supplier": supplier.name},
    )


def test_padron_query_cannot_be_linked_to_a_supplier_with_another_cuit(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "cuit": "30123456789", "supplier": None},
    )
    supplier = _Doc("Supplier", {"name": "Proveedor SA", "tax_id": "20123456789"})
    store = _store()
    store["ARCA CUIT Query"] = [query]
    store["Supplier"] = [supplier]
    module, frappe = _load(monkeypatch, store)

    with pytest.raises(ErrorDeFormulario, match="CUIT del proveedor no coincide"):
        module.vincular_consulta_a_proveedor(query.name, supplier.name)

    assert query.supplier is None
    assert frappe.db.writes == []


def test_padron_supplier_link_refuses_a_query_already_linked_to_a_customer(monkeypatch):
    query = _Doc(
        "ARCA CUIT Query",
        {"name": "QUERY-1", "cuit": "30123456789", "customer": "Cliente SA"},
    )
    supplier = _Doc(
        "Supplier",
        {"name": "Proveedor SA", "tax_id": "30-12345678-9"},
    )
    store = _store()
    store["ARCA CUIT Query"] = [query]
    store["Supplier"] = [supplier]
    module, frappe = _load(monkeypatch, store)

    with pytest.raises(ErrorDeFormulario, match="vinculada a un cliente"):
        module.vincular_consulta_a_proveedor(query.name, supplier.name)

    assert query.supplier is None
    assert frappe.db.writes == []


# --- emitir: the only action that consumes a number --------------------------


def _aprobado():
    return Cae(cae=CAE, vence="20261125", resultado="A")


def test_emitir_records_the_cae_the_number_and_the_traces(monkeypatch):
    doc = _comprobante(
        fecha_comprobante="", problema="Fecha de comprobante antigua inválida."
    )
    cliente = ClienteFalso(ultimo=0, respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.emitir_comprobante(doc.name)

    assert doc.estado == "Autorizado"
    assert doc.resultado == "A"
    assert doc.cae == CAE
    assert doc.cae_vence == "2026-11-25"
    assert doc.cbte_nro == 1
    assert doc.fecha_comprobante == "2026-11-12"
    assert doc.problema is None
    assert cliente.solicitud.cbte_fch == "20261112"
    assert doc.intentos == 1
    assert doc.solicitud_xml == "<FECAESolicitar/>"
    assert doc.respuesta_xml == "<Respuesta/>"
    assert resultado["estado"] == "Autorizado"
    assert CAE in resultado["mensaje"]


def test_invalid_invoice_posting_date_stops_before_the_emission_attempt(monkeypatch):
    store = _store([_comprobante()])
    store["Sales Invoice"][0].posting_date = ""
    cliente = ClienteFalso(
        ticket_error=AuthError("WSAA no debería ejecutarse con fecha inválida.")
    )
    module, frappe = _load(monkeypatch, store, cliente)

    with pytest.raises(ErrorDeFormulario, match="fecha de contabilización"):
        module.emitir_comprobante("ARCA-COMP-0")

    doc = store["ARCA Comprobante"][0]
    assert (doc.estado, doc.intentos, doc.cbte_nro) == ("Borrador", 0, 0)
    assert "Fecha de comprobante inválida" in doc.problema
    assert getattr(cliente, "numeracion", None) is None
    assert cliente.solicitud is None


def test_wsfe_request_is_captured_before_a_transport_failure(monkeypatch):
    from arca_integration.core.errors import TransportError

    module, _ = _load(monkeypatch, _store())
    module._site_file_path = lambda file_url: "/tmp/arca.crt"

    def falla_antes_de_responder(*args):
        raise TransportError("timeout")

    module._default_post = falla_antes_de_responder
    captura = {}
    client = module._wsfe_client(CONFIG, "/tmp/arca.key", EMPRESA, captura)

    with pytest.raises(TransportError):
        client.call("FECAESolicitar", b"<solicitud/>")

    assert captura["solicitud"] == b"<solicitud/>"
    assert "respuesta" not in captura


def test_emitir_asks_arca_for_the_next_number_every_time(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(ultimo=41, respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    module.emitir_comprobante(doc.name)

    assert doc.cbte_nro == 42
    assert cliente.solicitud.cbte_desde == 42
    assert cliente.solicitud.cbte_fch == "20261112"
    assert cliente.solicitud.condicion_iva_receptor == 5


def test_a_rejection_is_stored_with_its_reason(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(error=WsfeRejected("El punto de venta no está habilitado", code=10005))
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.emitir_comprobante(doc.name)

    assert doc.estado == "Rechazado"
    assert doc.resultado == "R"
    assert "10005" in doc.motivo_rechazo
    assert doc.cae is None
    assert "no quedó autorizado" in resultado["mensaje"]


def test_an_uncertain_answer_never_suggests_a_retry(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(error=WsfeUncertain("ARCA no confirmó ni rechazó el comprobante."))
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.emitir_comprobante(doc.name)

    assert doc.estado == "Incierto"
    assert doc.cbte_nro == 1
    assert "Conciliar" in resultado["mensaje"]
    assert "No lo reenvíes" in resultado["mensaje"]


def test_a_transport_failure_is_uncertain_not_rejected(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(error=TransportError("No se pudo conectar con ARCA."))
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    module.emitir_comprobante(doc.name)

    assert doc.estado == "Incierto"


def test_an_authorized_comprobante_is_never_sent_again(monkeypatch):
    doc = _comprobante(estado="Autorizado", cae=CAE)
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "no se vuelve a enviar" in str(excinfo.value)
    assert cliente.solicitud is None


def test_a_sales_invoice_with_an_authorized_comprobante_cannot_be_deleted(monkeypatch):
    comprobante = _comprobante(estado="Autorizado", cae=CAE, intentos=1)
    module, frappe = _load(monkeypatch, _store([comprobante]))
    factura = frappe.get_doc("Sales Invoice", "ACC-SINV-2026-00001")

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.evitar_borrar_factura_con_intento_arca(factura)

    assert comprobante.name in str(excinfo.value)
    assert CAE in str(excinfo.value)
    assert "trazabilidad fiscal" in str(excinfo.value)


def test_a_sales_invoice_without_an_emission_attempt_can_be_deleted(monkeypatch):
    comprobante = _comprobante(intentos=0)
    module, frappe = _load(monkeypatch, _store([comprobante]))
    factura = frappe.get_doc("Sales Invoice", "ACC-SINV-2026-00001")

    assert module.evitar_borrar_factura_con_intento_arca(factura) is None


def test_sales_invoice_delete_guard_is_registered():
    from arca_integration.hooks import doc_events

    assert (
        doc_events["Sales Invoice"]["on_trash"]
        == "arca_integration.api.evitar_borrar_factura_con_intento_arca"
    )


def test_an_uncertain_comprobante_must_be_reconciled_first(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "Conciliar" in str(excinfo.value)
    assert cliente.solicitud is None


def test_an_incomplete_snapshot_is_refused_before_any_request(monkeypatch):
    doc = _comprobante(condicion_iva_receptor=None)
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "condición frente al IVA" in str(excinfo.value)
    assert doc.estado == "Borrador"
    assert cliente.solicitud is None


@pytest.mark.parametrize(
    ("doc_tipo", "doc_nro", "mensaje"),
    [
        ("80", "", "el número de documento del receptor"),
        ("80", "20-00000000-1", "número de documento del receptor válido"),
    ],
)
def test_invalid_receiver_document_is_reported_only_when_emitting(
    monkeypatch, doc_tipo, doc_nro, mensaje
):
    doc = _comprobante(doc_tipo=doc_tipo, doc_nro=doc_nro)
    cliente = ClienteFalso(respuesta=_aprobado())
    module, _ = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario, match=mensaje):
        module.emitir_comprobante(doc.name)

    assert doc.estado == "Borrador"
    assert doc.intentos == 0
    assert cliente.tickets == 0
    assert cliente.solicitud is None


def test_a_missing_mapping_leaves_the_comprobante_in_draft(monkeypatch):
    store = _store([_comprobante()])
    store["ARCA Mapeo de Cuenta de Impuesto"] = []
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, store, cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante("ARCA-COMP-0")

    assert "IVA 21%" in str(excinfo.value)
    assert store["ARCA Comprobante"][0].estado == "Borrador"
    assert store["ARCA Comprobante"][0].intentos == 0
    assert any(values.get("problema") for _, _, values in frappe.db.writes)


def test_another_live_comprobante_for_the_same_invoice_blocks_the_attempt(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente, vivos=[("ARCA-COMP-9",)])

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "ARCA-COMP-9" in str(excinfo.value)
    assert doc.estado == "Borrador"
    assert cliente.solicitud is None


# --- the environment the operator read is the environment that is used -------


def _config(**cambios):
    return types.SimpleNamespace(**{**vars(CONFIG), **cambios})


def test_a_changed_environment_stops_the_emission_and_refreshes_the_proposal(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(
        monkeypatch, _store([doc]), cliente, config=_config(environment="Producción")
    )

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "cambió" in str(excinfo.value)
    assert doc.environment == "Producción"
    assert "revisá de nuevo" in doc.problema
    assert doc.estado == "Borrador"
    assert doc.intentos == 0
    assert doc.cbte_nro == 0
    assert cliente.solicitud is None


def test_emission_resolves_the_profile_from_the_voucher_snapshot(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(respuesta=_aprobado())
    module, _ = _load(monkeypatch, _store([doc]), cliente)
    resolved = []

    def settings(company, environment=None):
        resolved.append((company, environment))
        return _config(environment=environment)

    module._settings = settings
    module.emitir_comprobante(doc.name)

    assert resolved == [(EMPRESA, "Homologación")]


# --- the number has to survive a process that dies holding the answer -------


def test_an_authorized_comprobante_is_never_mutated_by_a_settings_change(monkeypatch):
    doc = _comprobante(estado="Autorizado", cae=CAE, cbte_nro=9, intentos=1)
    module, frappe = _load(
        monkeypatch, _store([doc]), ClienteFalso(respuesta=_aprobado()),
        config=_config(environment="Producción"),
    )

    with pytest.raises(ErrorDeFormulario):
        module.emitir_comprobante(doc.name)

    assert doc.environment == "Homologación"
    assert frappe.db.writes == []


def test_the_environment_refresh_never_touches_a_frozen_attempt(monkeypatch):
    """Overwriting the snapshot of a live attempt would disarm reconciliation."""
    doc = _comprobante(estado="Solicitando", cbte_nro=7, intentos=1)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(
        monkeypatch, _store([doc]), cliente, config=_config(environment="Producción")
    )

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "cambió" in str(excinfo.value)
    assert doc.environment == "Homologación"
    assert doc.estado == "Solicitando"

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.conciliar_comprobante(doc.name)

    assert "No se consulta" in str(excinfo.value)
    assert cliente.consulta is None


def test_the_number_is_stored_before_the_request_leaves(monkeypatch):
    doc = _comprobante()
    visto = {}

    def al_enviar():
        visto["cbte_nro"] = doc.cbte_nro
        visto["estado"] = doc.estado
        visto["commits"] = frappe.db.commits

    cliente = ClienteFalso(ultimo=4, respuesta=_aprobado(), al_enviar=al_enviar)
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    module.emitir_comprobante(doc.name)

    # At the instant the request is sent, the number is already durable: the
    # commit that stores it is the third one (lock, freeze, number).
    assert visto == {"cbte_nro": 5, "estado": "Solicitando", "commits": 3}
    assert [
        values["cbte_nro"]
        for _, _, values in frappe.db.writes
        if "cbte_nro" in values
    ] == [0, 5, 5]


class ClienteSinNumeracion(ClienteFalso):
    """ARCA never reported the next number: nothing was consumed."""

    def ultimo_autorizado(self, pto_venta, cbte_tipo):
        raise TransportError("No se pudo conectar con ARCA.")


def test_a_failure_before_the_number_leaves_nothing_consumed(monkeypatch):
    doc = _comprobante()
    cliente = ClienteSinNumeracion()
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "conectar" in str(excinfo.value)
    assert doc.estado == "Borrador"
    assert doc.intentos == 0
    assert doc.solicitado_at is None
    assert doc.cbte_nro == 0
    assert cliente.solicitud is None


def test_a_ticket_failure_stops_before_the_attempt_is_frozen(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(
        respuesta=_aprobado(), ticket_error=AuthError("WSAA no devolvió un ticket.")
    )
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario):
        module.emitir_comprobante(doc.name)

    assert doc.estado == "Borrador"
    assert doc.intentos == 0
    assert doc.cbte_nro == 0
    assert cliente.solicitud is None


def test_a_failure_after_the_number_was_assigned_never_returns_to_draft(monkeypatch):
    """The request carried a number, so ARCA may have it: the attempt is uncertain."""
    doc = _comprobante()
    cliente = ClienteFalso(error=AuthError("ARCA rechazó el ticket de WSFE."))
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert doc.estado == "Incierto"
    assert doc.cbte_nro == 1
    assert doc.intentos == 1
    assert "No lo reenvíes" in str(excinfo.value)
    assert "Conciliar" in str(excinfo.value)


def test_an_unusable_receiver_condition_costs_no_attempt(monkeypatch):
    """It passes the form check, then fails to build the request: after the freeze."""
    doc = _comprobante(condicion_iva_receptor="99")
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    # The freeze really happened and was then undone, not skipped.
    congelado = [
        values
        for _, _, values in frappe.db.writes
        if values.get("estado") == "Solicitando"
    ]
    assert congelado
    assert "condición frente al IVA" in str(excinfo.value)
    assert (doc.estado, doc.intentos, doc.cbte_nro) == ("Borrador", 0, 0)
    assert cliente.solicitud is None


class CorteDeProceso(BaseException):
    """The process died: nothing after this point in the code ran."""


def test_an_interruption_between_the_number_and_the_send_stays_reconcilable(monkeypatch):
    doc = _comprobante()

    def al_enviar():
        raise CorteDeProceso("proceso interrumpido")

    cliente = ClienteFalso(ultimo=6, respuesta=_aprobado(), al_enviar=al_enviar)
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(CorteDeProceso):
        module.emitir_comprobante(doc.name)

    # The number was stored before the send, so the attempt can be found again.
    assert doc.estado == "Solicitando"
    assert doc.cbte_nro == 7

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)
    assert "Conciliar" in str(excinfo.value)
    assert (doc.estado, doc.cbte_nro) == ("Solicitando", 7)

    # A 602 cannot prove whether an interrupted request was accepted.
    cliente.al_enviar = None
    cliente.respuesta = _consulta(existe=False, resultado=None, cae=None, vence=None)
    module.conciliar_comprobante(doc.name)

    assert doc.estado == "Incierto"
    assert "602" in doc.problema
    assert cliente.consulta == (1, 6, 7)


# --- one emission at a time, held by a row that survives the commits --------


def _bloqueo(detalle="Emisión de ACC-SINV-2026-00001", vence=None, token="otro"):
    clave = "emision:Demo SA"
    return _Doc(
        "ARCA Bloqueo de Emision",
        {
            "name": clave,
            "clave": clave,
            "company": EMPRESA,
            "detalle": detalle,
            "token": token,
            "vence": vence or datetime(2026, 11, 15, 10, 5, 0),
        },
    )


def test_an_emission_in_progress_blocks_the_same_comprobante(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)
    frappe.store["ARCA Bloqueo de Emision"] = [_bloqueo()]

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "emisión en curso" in str(excinfo.value)
    assert cliente.solicitud is None
    assert doc.estado == "Borrador"


def test_another_comprobante_competing_for_the_sequence_is_refused(monkeypatch):
    """Same company, other point of sale: it would compete for the same next number."""
    doc = _comprobante(pto_venta=2, cbte_tipo=1)
    cliente = ClienteFalso(respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)
    frappe.store["ARCA Bloqueo de Emision"] = [
        _bloqueo(detalle="Emisión de ACC-SINV-2026-00099")
    ]

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "emisión en curso" in str(excinfo.value)
    assert cliente.solicitud is None
    # The key is the company, not the sequence: that is what makes it effective.
    assert module._clave_de_bloqueo(EMPRESA) == "emision:Demo SA"


def test_an_expired_lock_is_reclaimed_instead_of_blocking_forever(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(ultimo=4, respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)
    frappe.store["ARCA Bloqueo de Emision"] = [
        _bloqueo(vence=datetime(2026, 11, 15, 9, 55, 0), token="muerto")
    ]

    module.emitir_comprobante(doc.name)

    assert doc.estado == "Autorizado"
    assert doc.cbte_nro == 5
    assert frappe.store["ARCA Bloqueo de Emision"] == []


def test_the_lock_is_released_after_a_rejection(monkeypatch):
    doc = _comprobante()
    cliente = ClienteFalso(
        error=WsfeRejected("El punto de venta no está habilitado", code=10005)
    )
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    module.emitir_comprobante(doc.name)

    assert doc.estado == "Rechazado"
    assert frappe.store["ARCA Bloqueo de Emision"] == []


def test_a_lost_lock_stops_the_emission_before_the_number_is_stored(monkeypatch):
    """A slow answer outlives the TTL: the next number must not be consumed twice."""
    doc = _comprobante()
    cliente = ClienteFalso(ultimo=4, respuesta=_aprobado())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    def tomar_bloqueo(company, detalle):
        frappe.store["ARCA Bloqueo de Emision"] = [_bloqueo(token="de_otro")]
        return "mio"

    module._tomar_bloqueo = tomar_bloqueo

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.emitir_comprobante(doc.name)

    assert "no se envió nada" in str(excinfo.value)
    assert doc.estado == "Borrador"
    assert doc.intentos == 0
    assert doc.cbte_nro == 0
    assert cliente.solicitud is None
    # The other process keeps its lock: releasing never steals a foreign token.
    assert [bloqueo.token for bloqueo in frappe.store["ARCA Bloqueo de Emision"]] == [
        "de_otro"
    ]


def test_the_lock_is_released_when_the_attempt_leaves_nothing_sent(monkeypatch):
    store = _store([_comprobante()])
    store["ARCA Mapeo de Cuenta de Impuesto"] = []
    module, frappe = _load(monkeypatch, store, ClienteFalso(respuesta=_aprobado()))

    with pytest.raises(ErrorDeFormulario):
        module.emitir_comprobante("ARCA-COMP-0")

    assert frappe.store["ARCA Bloqueo de Emision"] == []


def test_the_stored_state_is_read_again_once_the_lock_is_held(monkeypatch):
    en_memoria = _comprobante()
    guardado = _comprobante(estado="Incierto", cbte_nro=7)
    module, frappe = _load(monkeypatch, _store([guardado]))

    module._releer_estado(en_memoria)

    assert (en_memoria.estado, en_memoria.cbte_nro) == ("Incierto", 7)


# --- conciliar: read only, never a retry -------------------------------------


def _consulta(existe=True, resultado="A", cae=CAE, vence="20261125"):
    from arca_integration.core.wsfe.consulta import (
        ConsultaAlicIva,
        ConsultaComprobante,
    )

    return ConsultaComprobante(
        existe=existe,
        resultado=resultado,
        cae=cae,
        vence=vence,
        cbte_desde=7,
        cbte_hasta=7,
        imp_total=1210.0,
        concepto=1,
        doc_tipo=99,
        doc_nro=0,
        cbte_fch="20261112",
        imp_tot_conc=0.0,
        imp_neto=1000.0,
        imp_op_ex=0.0,
        imp_trib=0.0,
        imp_iva=210.0,
        mon_id="PES",
        mon_cotiz=1.0,
        condicion_iva_receptor=5,
        pto_venta=1,
        cbte_tipo=6,
        alicuotas=(ConsultaAlicIva(id=5, base_imp=1000.0, importe=210.0),),
        raw=b"<consulta/>",
    )


def test_reconciliation_is_blocked_while_an_emission_holds_the_company_lock(
    monkeypatch,
):
    doc = _comprobante(estado="Solicitando", cbte_nro=7, intentos=1)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)
    frappe.store["ARCA Bloqueo de Emision"] = [_bloqueo()]

    with pytest.raises(ErrorDeFormulario, match="emisión en curso"):
        module.conciliar_comprobante(doc.name)

    assert doc.estado == "Solicitando"
    assert cliente.consulta is None


def test_reconciliation_rechecks_the_state_after_acquiring_the_lock(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    def releer(doc):
        assert len(frappe.store["ARCA Bloqueo de Emision"]) == 1
        doc.estado = "Autorizado"

    module._releer_estado = releer

    with pytest.raises(ErrorDeFormulario, match="estado Incierto o Solicitando"):
        module.conciliar_comprobante(doc.name)

    assert cliente.consulta is None
    assert frappe.store["ARCA Bloqueo de Emision"] == []


def test_reconciling_an_authorized_comprobante_closes_the_attempt(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.conciliar_comprobante(doc.name)

    assert doc.estado == "Autorizado"
    assert doc.cae == CAE
    assert doc.cae_vence == "2026-11-25"
    assert doc.resultado == "A"
    assert cliente.consulta == (1, 6, 7)
    assert resultado["estado"] == "Autorizado"


def test_reconciling_absence_keeps_the_comprobante_uncertain(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_consulta(existe=False, resultado=None, cae=None, vence=None))
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.conciliar_comprobante(doc.name)

    assert doc.estado == "Incierto"
    assert doc.cae is None
    assert "602" in doc.problema
    assert "No lo reenvíes" in resultado["mensaje"]

    with pytest.raises(ErrorDeFormulario, match="estado Incierto"):
        module.emitir_comprobante(doc.name)

    assert cliente.solicitud is None


def test_reconciliation_from_a_dead_request_is_supported(monkeypatch):
    doc = _comprobante(estado="Solicitando", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    module.conciliar_comprobante(doc.name)

    assert doc.estado == "Autorizado"


def test_reconciliation_that_settles_nothing_stays_uncertain(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=7)
    cliente = ClienteFalso(error=TransportError("No se pudo conectar con ARCA."))
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.conciliar_comprobante(doc.name)

    assert doc.estado == "Incierto"
    assert "sigue sin confirmar" in resultado["mensaje"]


def test_only_an_uncertain_or_pending_comprobante_is_reconciled(monkeypatch):
    doc = _comprobante(estado="Borrador", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.conciliar_comprobante(doc.name)

    assert "Incierto o Solicitando" in str(excinfo.value)
    assert cliente.consulta is None


def test_a_comprobante_without_a_number_has_nothing_to_reconcile(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=0)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.conciliar_comprobante(doc.name)

    assert "no llegó a tener número" in str(excinfo.value)
    assert cliente.consulta is None


def test_a_pending_attempt_without_a_number_returns_to_draft_without_the_network(
    monkeypatch,
):
    """The number is written before the request leaves, so no number means unsent."""
    doc = _comprobante(
        estado="Solicitando", intentos=1, solicitado_at=datetime(2026, 11, 15, 9, 59, 0)
    )
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(monkeypatch, _store([doc]), cliente)

    resultado = module.conciliar_comprobante(doc.name)

    assert doc.estado == "Borrador"
    assert doc.intentos == 0
    assert doc.solicitado_at is None
    assert cliente.consulta is None
    assert "no se envió nada" in resultado["mensaje"]


def test_reconciliation_refuses_an_environment_it_was_not_sent_to(monkeypatch):
    doc = _comprobante(estado="Incierto", cbte_nro=7)
    cliente = ClienteFalso(respuesta=_consulta())
    module, frappe = _load(
        monkeypatch, _store([doc]), cliente, config=_config(environment="Producción")
    )

    with pytest.raises(ErrorDeFormulario) as excinfo:
        module.conciliar_comprobante(doc.name)

    assert "No se consulta" in str(excinfo.value)
    assert doc.estado == "Incierto"
    assert cliente.consulta is None
