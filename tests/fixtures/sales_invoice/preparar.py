"""Create the seven draft invoices the fixture capture reads.

Run inside a bench, after ``descubrir()`` told you which company, customer, item
and accounts exist. It creates **drafts only**: no ledger entry, no stock move, no
comprobante, no ARCA call. Nothing is submitted.

From ``bench --site <site> console``::

    exec(open("/tmp/arca-bench/preparar.py").read())
    descubrir()
    nombres = preparar(
        company="Mi Empresa",
        customer="Consumidor Final",
        item_code="ARCA-TEST",
        ingreso="Ventas Gravadas",
        iva_21="IVA 21",
        iva_105="IVA 10.5",
        iva_0="IVA 0",
    )

Then use the dict it prints as the argument of ``capturar(...)``.

The account arguments are ``account_name`` values, not full names: ERPNext names
an account ``<account_name> - <company abbr>``, and this module resolves that name
and creates the account only when it is missing.
"""

from __future__ import annotations

try:
    import frappe
except ImportError:  # pragma: no cover - only reachable outside a bench
    raise SystemExit(
        "preparar.py corre dentro de un bench: bench --site <site> console, "
        "después exec(open(...).read())."
    )

FECHA = "2026-11-12"


def especificaciones(
    company: str,
    customer: str,
    item_code: str,
    *,
    ingreso: str,
    iva_21: str,
    iva_105: str,
    iva_0: str,
) -> dict[str, dict]:
    """The seven invoices, as payloads. Pure: no database, no network."""

    def linea(qty, rate, **extra):
        datos = {
            "item_code": item_code,
            "qty": qty,
            "rate": rate,
            "income_account": ingreso,
        }
        datos.update(extra)
        return datos

    def impuesto(rate, cuenta):
        return {
            "charge_type": "On Net Total",
            "account_head": cuenta,
            "rate": rate,
            "included_in_print_rate": 0,
            "description": f"IVA {rate} %",
        }

    casos = {
        "A": {"items": [linea(1, 1000)], "taxes": [impuesto(21, iva_21)]},
        "B": {"items": [linea(1, 2500)], "taxes": [impuesto(10.5, iva_105)]},
        "C": {
            "items": [linea(3, 333.33), linea(2, 500)],
            "taxes": [impuesto(21, iva_21)],
        },
        "D": {"items": [linea(1, 800)], "taxes": [impuesto(0, iva_0)]},
        "E": {
            "items": [linea(1, 1000)],
            "taxes": [impuesto(21, iva_21)],
            "discount_amount": 100,
            "apply_discount_on": "Grand Total",
        },
        "F": {
            "items": [
                linea(1, 1000, price_list_rate=1000, discount_percentage=10)
            ],
            "taxes": [impuesto(21, iva_21)],
        },
        "G": {
            "items": [linea(1, 1000), linea(1, 1000)],
            "taxes": [impuesto(21, iva_21), impuesto(10.5, iva_105)],
        },
    }

    comun = {
        "doctype": "Sales Invoice",
        "company": company,
        "customer": customer,
        "posting_date": FECHA,
    }
    return {caso: {**comun, **datos} for caso, datos in casos.items()}


def asegurar_cuenta(company: str, nombre: str, root_type: str) -> str:
    """Return the full account name, creating the account under a group if needed."""
    existentes = frappe.get_all(
        "Account",
        filters={"company": company, "account_name": nombre, "is_group": 0},
        pluck="name",
        limit=1,
    )
    if existentes:
        return existentes[0]

    padres = frappe.get_all(
        "Account",
        filters={"company": company, "root_type": root_type, "is_group": 1},
        pluck="name",
        limit=1,
    )
    if not padres:
        raise SystemExit(
            f"La compañía {company!r} no tiene ninguna cuenta de grupo con root_type "
            f"{root_type!r} donde colgar {nombre!r}. Creá una desde el árbol de "
            "cuentas y volvé a intentar."
        )

    doc = frappe.get_doc(
        {
            "doctype": "Account",
            "account_name": nombre,
            "company": company,
            "parent_account": padres[0],
        }
    )
    doc.insert()
    return doc.name


def preparar(
    company: str,
    customer: str,
    item_code: str,
    *,
    ingreso: str,
    iva_21: str,
    iva_105: str,
    iva_0: str,
) -> dict[str, str]:
    """Create the seven drafts and return ``{caso: nombre}``."""
    cuentas = {
        "ingreso": asegurar_cuenta(company, ingreso, "Income"),
        "iva_21": asegurar_cuenta(company, iva_21, "Liability"),
        "iva_105": asegurar_cuenta(company, iva_105, "Liability"),
        "iva_0": asegurar_cuenta(company, iva_0, "Liability"),
    }

    print(f"Compañía: {company}")
    for rol, nombre in cuentas.items():
        print(f"  {rol:8s} {nombre}")

    nombres: dict[str, str] = {}
    for caso, datos in especificaciones(
        company, customer, item_code, **cuentas
    ).items():
        doc = frappe.get_doc(datos)
        doc.insert()
        nombres[caso] = doc.name
        print(
            f"{caso}: {doc.name}  neto={doc.net_total} "
            f"iva={doc.total_taxes_and_charges} total={doc.grand_total} "
            f"redondeo={doc.rounding_adjustment}"
        )

    print("\ncapturar(" + repr(nombres) + ", destino=\"/tmp/arca-fixtures\")")
    return nombres


def descubrir(company: str | None = None) -> None:
    """Print what the site already has, so nothing is created blindly."""
    print(f"Sitio: {frappe.local.site}")

    print("\nCompañías:")
    for c in frappe.get_all(
        "Company", fields=["name", "abbr", "default_currency"], order_by="name"
    ):
        print(f"  {c.name} | abbr {c.abbr} | {c.default_currency}")

    objetivo = [c for c in frappe.get_all("Company", pluck="name") if not company or c == company]

    for nombre in objetivo:
        cuentas = frappe.get_all(
            "Account",
            filters={"company": nombre, "is_group": 0},
            fields=["name", "account_name", "root_type"],
            order_by="name",
        )
        ingresos = [a for a in cuentas if a.root_type == "Income"]
        impuestos = [
            a
            for a in cuentas
            if a.root_type in ("Liability", "Asset")
            and any(pista in a.name.upper() for pista in ("IVA", "VAT", "IMPUESTO"))
        ]

        print(f"\nCompañía {nombre!r}")
        print(f"  cuentas de ingreso ({len(ingresos)}):")
        for a in ingresos[:20]:
            print(f"    {a.name}")
        print(f"  cuentas candidatas a IVA ({len(impuestos)}):")
        for a in impuestos[:20]:
            print(f"    {a.name}  [{a.root_type}]")

    print("\nClientes (5):")
    for nombre in frappe.get_all("Customer", pluck="name", limit=5, order_by="name"):
        print(f"  {nombre}")

    print("\nÍtems de servicio (5):")
    for nombre in frappe.get_all(
        "Item",
        filters={"is_stock_item": 0, "disabled": 0},
        pluck="name",
        limit=5,
        order_by="name",
    ):
        print(f"  {nombre}")
