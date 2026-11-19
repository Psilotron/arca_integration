"""Capture the ``Sales Invoice`` fixtures the mapping reads.

This file is meant for a bench, not for this repository's suite: it imports
``frappe`` and reads a real site. It never submits, never creates a comprobante
and never calls ARCA.

From ``bench --site <site> console``::

    exec(open("/tmp/arca-bench/capturar.py").read())
    capturar({"A": "SINV-2026-00001", "B": "SINV-2026-00002"}, destino="/tmp/arca-fixtures")

``bench console`` has no ``__file__``, so the destination comes from the
``destino`` argument or from ``ARCA_FIXTURE_DIR`` (default
``/tmp/arca-fixtures``); the file is never written into the checkout.

Only the fields ``invoice_amounts`` reads are written, so a renamed field in
ERPNext shows up as a missing key instead of hiding inside a full ``as_dict()``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import frappe
except ImportError:  # pragma: no cover - only reachable outside a bench
    raise SystemExit(
        "capturar.py corre dentro de un bench: bench --site <site> console, "
        "después exec(open(...).read())."
    )


# ``bench console`` runs the file with ``exec(open(...).read())``, so ``__file__``
# does not exist. The destination therefore comes from the environment, and the
# default lives outside the app so a capture never writes into the repository.
DESTINO = Path(os.environ.get("ARCA_FIXTURE_DIR", "/tmp/arca-fixtures"))

CAMPOS_FACTURA = (
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
)

CAMPOS_LINEA = ("idx", "income_account", "net_amount", "item_tax_template", "item_tax_rate")

CAMPOS_IMPUESTO = (
    "idx",
    "charge_type",
    "account_head",
    "rate",
    "tax_amount",
    "included_in_print_rate",
)

DESCRIPCIONES = {
    "A": "Un ítem al 21 %, sin nada más.",
    "B": "10,5 %, y el residual sub-centavo.",
    "C": "Dos líneas al 21 %, donde la fila de IVA redondea.",
    "D": "0 % es una alícuota, no una exención.",
    "E": "Rechazada: descuento global.",
    "F": "Descuento por línea: net_amount ya lo lleva.",
    "G": "Rechazada: dos alícuotas.",
}


def capturar(casos: dict[str, str], *, destino: Path | None = None, sobrescribir: bool = False):
    """Write one JSON per ``{caso: nombre de Sales Invoice}`` and return the paths."""
    destino = Path(destino or DESTINO)
    destino.mkdir(parents=True, exist_ok=True)

    for caso, nombre in casos.items():
        path = destino / f"caso_{caso.lower()}.json"
        if path.exists() and not sobrescribir:
            anterior = json.loads(path.read_text())
            if anterior.get("_source") == "captured":
                raise SystemExit(
                    f"{path} ya está capturado. Pasá sobrescribir=True para reemplazarlo."
                )

        data = _extraer(frappe.get_doc("Sales Invoice", nombre))
        data["_source"] = "captured"
        data["_caso"] = caso.upper()
        data["_descripcion"] = DESCRIPCIONES.get(caso.upper(), "")
        path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        print(f"escrito {path} desde {nombre}")

    return destino


def _extraer(doc) -> dict:
    data = {campo: doc.get(campo) for campo in CAMPOS_FACTURA}
    data["items"] = [
        {campo: item.get(campo) for campo in CAMPOS_LINEA} for item in doc.get("items", [])
    ]
    data["taxes"] = [
        {campo: tax.get(campo) for campo in CAMPOS_IMPUESTO} for tax in doc.get("taxes", [])
    ]

    # add_deduct_tax exists on some versions and was removed in v15; keep it when
    # present so a captured fixture records what the site actually has.
    for origen, destino in zip(doc.get("taxes", []), data["taxes"]):
        if origen.meta.has_field("add_deduct_tax"):
            destino["add_deduct_tax"] = origen.get("add_deduct_tax")

    data["posting_date"] = str(data["posting_date"]) if data["posting_date"] else None
    return data
