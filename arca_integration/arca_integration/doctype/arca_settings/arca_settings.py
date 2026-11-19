"""ARCA Settings: one profile per company and ARCA environment.

There is no ``Single`` document anymore. Every company configures its own
certificate and key. Endpoints are resolved from the selected environment by
the backend, and the issuer CUIT is read from the company's own ``Tax ID``.
"""

import frappe
from frappe.model.document import Document

from arca_integration.core.errors import AuthError
from arca_integration.core.endpoints import (
    UnknownArcaEnvironment,
    endpoints_para_ambiente,
)
from arca_integration.core.padron import normalize_cuit
from arca_integration.core.wsaa import (
    load_private_key_data,
    validate_certificate_cuit,
    validate_key_pair,
)


class ConfigurationError(Exception):
    """Raised by the helpers below so they can be exercised without a bench."""


def get_company_cuit(company: str) -> str:
    """The issuer CUIT comes from the ERPNext Company record."""
    company_tax_id = frappe.db.get_value("Company", company, "tax_id")

    if not company_tax_id:
        raise ConfigurationError(f"La empresa {company} no tiene CUIT configurado.")

    try:
        return normalize_cuit(company_tax_id)
    except ValueError as exc:
        raise ConfigurationError(
            f"El identificador fiscal de {company} no es un CUIT válido: {company_tax_id!r}."
        ) from exc


def get_certificate_path(certificate: str) -> str:
    return frappe.get_doc("File", {"file_url": certificate}).get_full_path()


def store_pasted_key(document) -> str | None:
    """Move the pasted PEM into the ``Password`` field.

    The paste is only a way in: Frappe's ``Password`` field is what encrypts the
    value into ``__Auth`` and replaces it with a mask before anything is written
    to this DocType's table. Clearing the paste here keeps the plaintext out of
    the row for good.
    """
    pegado = (document.get("private_key_paste") or "").replace("\r\n", "\n").replace(
        "\r", "\n"
    )

    if not pegado.strip():
        document.set("private_key_paste", None)
        return None

    load_private_key_data(pegado.encode("utf-8"))
    document.set("private_key", pegado)
    document.set("private_key_paste", None)
    return pegado


def validate_profile(document) -> None:
    frappe.db.get_value("Company", document.company, "name", for_update=True)
    name = document.get("name")
    existente = frappe.db.get_value(
        "ARCA Settings",
        {"company": document.company, "environment": document.environment},
        "name",
    )
    if existente and existente != name:
        frappe.throw(
            f"Ya existe una configuración ARCA de {document.environment} "
            f"para {document.company}."
        )

    predeterminado = frappe.db.get_value(
        "ARCA Settings",
        {"company": document.company, "is_default": 1},
        "name",
    )
    es_predeterminado = document.get("is_default") in (1, "1", True)
    if es_predeterminado:
        if predeterminado and predeterminado != name:
            frappe.db.set_value(
                "ARCA Settings",
                predeterminado,
                "is_default",
                0,
                update_modified=False,
            )
    elif name and predeterminado == name:
        frappe.throw(
            "Cada empresa debe conservar un ambiente ARCA predeterminado. "
            "Elegí otro perfil como predeterminado antes de quitar este."
        )
    elif not predeterminado:
        document.is_default = 1


class ARCASettings(Document):
    def validate(self):
        try:
            self.cuit_representada = get_company_cuit(self.company)
            validate_profile(self)
            if self.get("cbte_tipo") not in (None, "", 1, "1", 6, "6"):
                frappe.throw(
                    "El tipo sugerido debe ser 1 (Factura A) o 6 (Factura B)."
                )
            endpoints_para_ambiente(self.environment)

            key = store_pasted_key(self)
            if self.certificate:
                cert_path = get_certificate_path(self.certificate)
                validate_certificate_cuit(cert_path, self.cuit_representada)
                if key is None:
                    key = self.get_password("private_key", raise_exception=False)
                if key:
                    validate_key_pair(cert_path, key.encode("utf-8"))
        except (ConfigurationError, UnknownArcaEnvironment, AuthError) as exc:
            message = getattr(exc, "message", str(exc))
            detail = getattr(exc, "detail", None)
            frappe.throw(message if not detail else f"{message} ({detail})")

    def on_trash(self):
        if not self.get("is_default"):
            return

        frappe.db.get_value("Company", self.company, "name", for_update=True)
        other_profiles = frappe.get_all(
            "ARCA Settings",
            filters={"company": self.company, "name": ["!=", self.name]},
            pluck="name",
            limit=1,
        )
        if other_profiles:
            frappe.throw(
                "No se puede eliminar el ambiente predeterminado mientras exista "
                "otro perfil. Marcá primero otro ambiente como predeterminado."
            )
