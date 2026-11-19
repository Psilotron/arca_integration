import ast
import json
from pathlib import Path

API = Path(__file__).parent.parent / "arca_integration" / "api.py"


def test_api_module_exposes_consultar():
    tree = ast.parse(API.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "consultar" in functions
    decorators = " ".join(ast.unparse(node) for node in functions["consultar"].decorator_list)
    assert "whitelist" in decorators


def test_api_module_exposes_the_point_of_sale_catalog():
    tree = ast.parse(API.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "consultar_puntos_venta" in functions
    decorators = " ".join(
        ast.unparse(node) for node in functions["consultar_puntos_venta"].decorator_list
    )
    assert "whitelist" in decorators


def test_form_script_adds_a_consultar_button():
    script = (
        Path(__file__).parent.parent
        / "arca_integration"
        / "arca_integration"
        / "doctype"
        / "arca_cuit_query"
        / "arca_cuit_query.js"
    )

    text = script.read_text()
    assert "arca_integration.api.consultar" in text
    assert "__(\"Consultar\")" in text
    assert "environment: frm.doc.environment" in text
    assert "supplier: frm.doc.supplier" in text
    assert "Elegí Homologación o Producción" in text


def test_settings_form_uses_the_point_of_sale_catalog():
    script = (
        Path(__file__).parent.parent
        / "arca_integration"
        / "arca_integration"
        / "doctype"
        / "arca_settings"
        / "arca_settings.js"
    )

    text = script.read_text()
    assert "arca_integration.api.consultar_puntos_venta" in text
    assert 'frm.set_value("pto_venta", punto.nro)' in text
    assert "no se modificó" in text


def test_api_reports_missing_configuration_as_an_arca_error():
    source = API.read_text()

    assert "raise AuthError" in source
    assert "raise frappe.ValidationError" not in source


def test_consultar_creates_a_history_entry_without_bypassing_permissions():
    source = API.read_text()

    assert "name: str | None = None" in source
    assert 'frappe.new_doc("ARCA CUIT Query")' in source
    assert "doc.save()" in source
    assert "doc.save(ignore_permissions=True)" not in source
    assert "doc.insert(ignore_permissions=True)" not in source


def test_padron_workflow_endpoints_are_whitelisted():
    tree = ast.parse(API.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    for name in (
        "perfiles_padron",
        "vincular_consulta_a_cliente",
        "vincular_consulta_a_proveedor",
    ):
        decorators = " ".join(
            ast.unparse(node) for node in functions[name].decorator_list
        )
        assert "whitelist" in decorators


def test_the_api_never_reads_a_single_settings_document():
    """`ARCA Settings` is per company; a `get_single` here would read the wrong one.

    That the resolver actually picks the company's document is checked in
    ``test_api_settings.py``; this only pins that the `Single` path is gone.
    """
    source = API.read_text()

    assert "get_single" not in source


def test_the_form_passes_the_company_to_the_endpoint():
    script = (
        Path(__file__).parent.parent
        / "arca_integration"
        / "arca_integration"
        / "doctype"
        / "arca_cuit_query"
        / "arca_cuit_query.js"
    ).read_text()

    assert "company: frm.doc.company" in script


def test_padron_workflow_is_loaded_for_customer_and_sales_invoice():
    hooks = Path(__file__).parents[1] / "arca_integration" / "hooks.py"
    tree = ast.parse(hooks.read_text())
    doctype_js = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "doctype_js"
            for target in node.targets
        )
    )

    assert doctype_js["Customer"] == [
        "public/js/padron_lookup.js",
        "public/js/customer.js",
    ]
    assert doctype_js["Supplier"] == [
        "public/js/padron_lookup.js",
        "public/js/supplier.js",
    ]
    assert doctype_js["Sales Invoice"] == [
        "public/js/padron_lookup.js",
        "public/js/sales_invoice.js",
    ]


def test_padron_dialog_requires_explicit_profile_and_application_choice():
    root = Path(__file__).parents[1] / "arca_integration" / "public" / "js"
    lookup = (root / "padron_lookup.js").read_text()
    customer = (root / "customer.js").read_text()
    supplier = (root / "supplier.js").read_text()
    invoice = (root / "sales_invoice.js").read_text()

    assert 'options: ["", ...opciones].join("\\n")' in lookup
    assert "default: 0" in lookup
    assert "company: frm.doc.company" in invoice
    assert "frm.__arca_padron_queries" in customer
    assert "frm.__arca_padron_queries" in supplier
    assert "vincular_consulta_a_proveedor" in supplier
    assert "Historial de consultas ARCA" in invoice
    assert '__("Comprobante ARCA")' in invoice
    assert '__("ARCA"),\n\t\t);' in invoice


def test_cuit_query_stores_workflow_links_and_not_found_status():
    path = (
        Path(__file__).parents[1]
        / "arca_integration"
        / "arca_integration"
        / "doctype"
        / "arca_cuit_query"
        / "arca_cuit_query.json"
    )
    doctype = json.loads(path.read_text())
    fields = {field["fieldname"]: field for field in doctype["fields"]}

    assert fields["customer"]["options"] == "Customer"
    assert fields["supplier"]["options"] == "Supplier"
    assert fields["sales_invoice"]["options"] == "Sales Invoice"
    assert "No encontrado" in fields["status"]["options"].splitlines()
    permissions = {permission["role"]: permission for permission in doctype["permissions"]}
    assert permissions["Sales User"]["create"] == 1
    assert permissions["Sales User"]["read"] == 1
    assert permissions["Accounts User"]["create"] == 1
    assert permissions["Accounts User"]["read"] == 1
    assert permissions["Purchase User"]["create"] == 1
    assert permissions["Purchase User"]["read"] == 1
