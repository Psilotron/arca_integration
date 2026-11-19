"""Frappe-facing endpoints: taxpayer lookup and comprobante emission.

The core logic lives in `arca_integration.core`; this module only resolves the
configuration of one company, runs the operation and records the result. No
fiscal rule is decided here.
"""

import hashlib
import secrets
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import frappe
from frappe.exceptions import DuplicateEntryError
from frappe.utils import now_datetime

from arca_integration.core import comprobante as fiscal
from arca_integration.core.client import TIMEOUT_SECONDS, ArcaClient, _default_post
from arca_integration.core.comprobante import ComprobanteError, Estado
from arca_integration.core.endpoints import endpoints_para_ambiente
from arca_integration.core.errors import ArcaError, AuthError, PersonaNotFound, WsfeError
from arca_integration.core.factura import invoice_amounts, receiver_document
from arca_integration.core.mapeo import AccountMapping
from arca_integration.core.padron import normalize_cuit
from arca_integration.core.ticket_cache import FileTicketCache
from arca_integration.core.wsfe.alicuotas import rate_for_id
from arca_integration.core.wsfe.cae import AlicIva, CaeRequest
from arca_integration.core.wsfe.client import WsfeClient
from arca_integration.core.wsfe.types import CondicionIvaReceptor

#: States the operator may still correct: nothing reached ARCA, or what reached
#: it was refused.
EDITABLES = (Estado.BORRADOR.value, Estado.RECHAZADO.value)

#: States whose outcome only a read of ARCA can settle.
CONCILIABLES = (Estado.INCIERTO.value, Estado.SOLICITANDO.value)

#: The two WSFE operations whose bytes are worth keeping as fiscal evidence.
TRAZABLES = ("FECAESolicitar", "FECompConsultar")

#: How long an emission lock survives if its process dies. It outlives the
#: slowest WSAA + WSFE exchange (two 30-second timeouts) and still expires, so a
#: killed process cannot block the company forever.
LOCK_TTL_SECONDS = 300



def _site_file_path(file_url: str | None) -> str:
    if not file_url:
        raise AuthError("Falta el certificado de ARCA en ARCA Settings.")
    return frappe.get_doc("File", {"file_url": file_url}).get_full_path()


def _ticket_cache(company: str, environment: str) -> FileTicketCache:
    """Keep tickets separate for each company and ARCA environment."""
    key = hashlib.sha256(f"{company}\0{environment}".encode("utf-8")).hexdigest()[:16]
    return FileTicketCache(
        frappe.get_site_path("private", "files", f"arca_ta_{key}.json")
    )


def _settings(company: str | None, environment: str | None = None):
    if not company:
        raise AuthError("Falta la empresa para resolver la configuración ARCA.")

    filters = {"company": company}
    if environment is not None:
        filters["environment"] = environment
    else:
        filters["is_default"] = 1

    name = frappe.db.get_value("ARCA Settings", filters, "name")
    if not name:
        if environment is not None:
            raise AuthError(
                f"La empresa {company} no tiene configuración ARCA de "
                f"{environment or 'sin definir'}."
            )
        raise AuthError(
            f"La empresa {company} no tiene una configuración ARCA con ambiente "
            "predeterminado."
        )

    return frappe.get_doc("ARCA Settings", name)


def _check_company_access(company: str | None) -> str:
    if not company:
        frappe.throw("Falta la empresa para comprobar los permisos.")

    frappe.get_doc("Company", company).check_permission("read")
    return company


@contextmanager
def _private_key_file(settings):
    try:
        key = settings.get_password("private_key", raise_exception=True)
    except frappe.ValidationError as exc:
        raise AuthError("Falta la clave privada de ARCA en ARCA Settings.") from exc
    handle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    handle.write(key)
    handle.close()
    path = Path(handle.name)
    path.chmod(0o600)
    try:
        yield str(path)
    finally:
        path.unlink(missing_ok=True)


def _client(settings, key_path: str, company: str) -> ArcaClient:
    endpoints = endpoints_para_ambiente(settings.environment)
    return ArcaClient(
        wsaa_url=endpoints["wsaa_url"],
        padron_url=endpoints["padron_url"],
        cert_path=_site_file_path(settings.certificate),
        key_path=key_path,
        cuit_representada=settings.cuit_representada,
        cache=_ticket_cache(company, settings.environment),
    )


def _wsfe_client(
    settings, key_path: str, company: str, captura: dict | None = None
) -> WsfeClient:
    """One WSFE client, optionally recording the request and the answer.

    The traces are taken from the transport, not rebuilt from the request
    object: what the operator sees has to be the bytes ARCA saw.
    """

    def post(url: str, body: bytes, action: str) -> bytes:
        if captura is not None and any(name in action for name in TRAZABLES):
            captura["solicitud"] = body

        answer = _default_post(url, body, TIMEOUT_SECONDS, action)

        if captura is not None and any(name in action for name in TRAZABLES):
            captura["respuesta"] = answer

        return answer

    endpoints = endpoints_para_ambiente(settings.environment)
    return WsfeClient(
        wsaa_url=endpoints["wsaa_url"],
        wsfe_url=endpoints["wsfe_url"],
        cert_path=_site_file_path(settings.certificate),
        key_path=key_path,
        cuit_representada=settings.cuit_representada,
        cache=_ticket_cache(company, settings.environment),
        http_post=post,
    )


def _account_mapping(company: str) -> AccountMapping:
    """Both mapping tables of one company, as the pure layer wants them."""
    campos = ["company", "account", "naturaleza", "activo"]

    return AccountMapping.from_rows(
        income_rows=frappe.get_all(
            "ARCA Mapeo de Cuenta de Ingreso",
            filters={"company": company},
            fields=campos,
        ),
        tax_rows=frappe.get_all(
            "ARCA Mapeo de Cuenta de Impuesto",
            filters={"company": company},
            fields=campos,
        ),
    )


def _company_currency(company: str) -> str:
    currency = frappe.db.get_value("Company", company, "default_currency")

    if not currency:
        raise ComprobanteError(
            f"La compañía {company} no tiene moneda por defecto, y sin ella no se "
            "puede comprobar que la factura esté en la moneda correcta."
        )

    return currency


def _invoice_amounts(invoice, company: str):
    return invoice_amounts(
        invoice, _account_mapping(company), company_currency=_company_currency(company)
    )


def _invoice_date(invoice):
    posting_date = invoice.get("posting_date")
    try:
        fiscal.formatear_fecha(posting_date)
    except ComprobanteError as exc:
        raise ComprobanteError(
            f"La fecha de contabilización de la factura {invoice.name} no es válida: "
            f"{exc}"
        ) from exc
    return posting_date


def _target_document(
    cuit: str,
    company: str,
    environment: str,
    customer: str | None = None,
    sales_invoice: str | None = None,
    supplier: str | None = None,
):
    # Each call is an audit entry; a previous result is never overwritten.
    frappe.has_permission("ARCA CUIT Query", ptype="create", throw=True)
    doc = frappe.new_doc("ARCA CUIT Query")

    doc.cuit = cuit
    doc.company = company
    doc.environment = environment
    doc.customer = customer
    doc.sales_invoice = sales_invoice
    doc.supplier = supplier
    doc.queried_at = now_datetime()
    doc.razon_social = None
    doc.tipo_persona = None
    doc.estado_clave = None
    doc.raw_response = None
    doc.error_message = None
    return doc


def _record(
    cuit: str,
    company: str,
    environment: str,
    status: str,
    doc=None,
    **values,
):
    doc = doc or _target_document(cuit, company, environment)
    doc.status = status
    for fieldname, value in values.items():
        setattr(doc, fieldname, value)
    doc.save()
    return doc


def _consulta_contexto(registro, cuit: str, company: str, environment: str) -> dict:
    return {
        "name": registro.name,
        "cuit": cuit,
        "company": company,
        "environment": environment,
        "queried_at": registro.queried_at,
    }


def _canonical_cuit(raw: str) -> str:
    try:
        return normalize_cuit(raw)
    except ValueError:
        return (raw or "").strip()


@frappe.whitelist()
def consultar_puntos_venta(
    name: str,
) -> list[dict[str, int | str | bool | None]]:
    if not name:
        frappe.throw("Falta el perfil de ARCA para consultar los puntos de venta.")

    settings = frappe.get_doc("ARCA Settings", name)
    settings.check_permission("read")
    company = _check_company_access(settings.company)

    try:
        with _private_key_file(settings) as key_path:
            puntos = _wsfe_client(settings, key_path, company).ptos_venta()
    except ArcaError as exc:
        if isinstance(exc, WsfeError) and exc.code == 602:
            message = (
                "ARCA devolvió el error 602 al consultar los puntos de venta. "
                "Esto no confirma que el punto configurado sea inválido; el valor "
                "guardado no se modificó."
            )
        else:
            message = getattr(exc, "message", str(exc))
        if exc.detail:
            message = f"{message} ({exc.detail})"
        frappe.throw(message)
        raise exc

    return [
        {
            "nro": punto.nro,
            "emision_tipo": punto.emision_tipo,
            "bloqueado": punto.bloqueado,
            "fch_baja": punto.fch_baja,
        }
        for punto in puntos
    ]


@frappe.whitelist()
def consultar(
    cuit: str,
    name: str | None = None,
    company: str | None = None,
    environment: str | None = None,
    customer: str | None = None,
    sales_invoice: str | None = None,
    supplier: str | None = None,
) -> dict:
    company = _check_company_access(company)
    if environment not in ("Homologación", "Producción"):
        frappe.throw("Elegí el ambiente de ARCA: Homologación o Producción.")

    cuit = _canonical_cuit(cuit)
    if sales_invoice:
        if supplier:
            frappe.throw("Una factura de venta no puede vincularse a un proveedor.")
        invoice = frappe.get_doc("Sales Invoice", sales_invoice)
        invoice.check_permission("read")
        if invoice.company != company:
            frappe.throw("La empresa no coincide con la factura seleccionada.")
        if customer and invoice.customer != customer:
            frappe.throw("El cliente no coincide con la factura seleccionada.")
        customer = invoice.customer
        invoice_cuit = _canonical_cuit(invoice.get("tax_id") or "")
        if invoice_cuit and invoice_cuit != cuit:
            frappe.throw("El CUIT no coincide con el de la factura seleccionada.")
    if customer and supplier:
        frappe.throw("Elegí un cliente o un proveedor para la consulta.")
    if customer:
        customer_doc = frappe.get_doc("Customer", customer)
        customer_doc.check_permission("read")
        customer_cuit = _canonical_cuit(customer_doc.get("tax_id") or "")
        if customer_cuit and customer_cuit != cuit:
            frappe.throw("El CUIT no coincide con el del cliente seleccionado.")
    if supplier:
        supplier_doc = frappe.get_doc("Supplier", supplier)
        supplier_doc.check_permission("read")
        supplier_cuit = _canonical_cuit(supplier_doc.get("tax_id") or "")
        if supplier_cuit and supplier_cuit != cuit:
            frappe.throw("El CUIT no coincide con el del proveedor seleccionado.")
    registro = _target_document(
        cuit,
        company,
        environment,
        customer=customer,
        sales_invoice=sales_invoice,
        supplier=supplier,
    )
    try:
        settings = _settings(company, environment)
    except AuthError as exc:
        stored = getattr(exc, "message", str(exc))
        registro = _record(
            cuit,
            company,
            environment,
            "Error",
            doc=registro,
            error_message=stored,
        )
        contexto = _consulta_contexto(registro, cuit, company, environment)
        return {
            **contexto,
            "status": "Error",
            "razon_social": None,
            "tipo_persona": None,
            "estado_clave": None,
            "error": stored,
        }

    try:
        with _private_key_file(settings) as key_path:
            persona = _client(settings, key_path, company).lookup(cuit)
    except (ArcaError, ValueError) as exc:
        status = "No encontrado" if isinstance(exc, PersonaNotFound) else "Error"
        message = getattr(exc, "message", str(exc))
        detail = getattr(exc, "detail", None)
        stored = message if not detail else f"{message} ({detail})"
        registro = _record(
            cuit,
            company,
            environment,
            status,
            doc=registro,
            error_message=stored,
        )
        contexto = _consulta_contexto(registro, cuit, company, environment)
        return {
            **contexto,
            "status": status,
            "razon_social": None,
            "tipo_persona": None,
            "estado_clave": None,
            "error": stored,
        }

    registro = _record(
        cuit,
        company,
        environment,
        "OK",
        doc=registro,
        razon_social=persona.razon_social,
        tipo_persona=persona.tipo_persona,
        estado_clave=persona.estado_clave,
        raw_response=persona.raw,
    )
    contexto = _consulta_contexto(registro, cuit, company, environment)
    return {
        **contexto,
        "status": "OK",
        "razon_social": persona.razon_social,
        "tipo_persona": persona.tipo_persona,
        "estado_clave": persona.estado_clave,
        "error": None,
    }


@frappe.whitelist()
def perfiles_padron() -> list[dict]:
    """Return only ARCA profiles visible to the current user."""
    # This endpoint exposes only routing metadata. The credential-bearing
    # settings document remains server-side and is resolved by consultar().
    get_profiles = getattr(frappe, "get_list", frappe.get_all)
    profiles = get_profiles(
        "ARCA Settings",
        fields=["name", "company", "environment", "is_default"],
        order_by="company asc, is_default desc, environment asc",
    )
    return [
        profile
        for profile in profiles
        if frappe.has_permission(
            "Company",
            ptype="read",
            doc=frappe.get_doc("Company", profile["company"]),
        )
    ]


@frappe.whitelist()
def vincular_consulta_a_cliente(query: str, customer: str) -> None:
    """Attach a pre-save lookup to the Customer created from its result."""
    registro = frappe.get_doc("ARCA CUIT Query", query)
    registro.check_permission("read")
    cliente = frappe.get_doc("Customer", customer)
    cliente.check_permission("write")
    if _canonical_cuit(cliente.get("tax_id") or "") != _canonical_cuit(
        registro.cuit or ""
    ):
        frappe.throw("El CUIT del cliente no coincide con la consulta ARCA.")
    if registro.customer and registro.customer != customer:
        frappe.throw("La consulta ya está vinculada a otro cliente.")
    if registro.supplier:
        frappe.throw("La consulta ya está vinculada a un proveedor.")
    frappe.db.set_value(
        "ARCA CUIT Query", registro.name, {"customer": customer}
    )


@frappe.whitelist()
def vincular_consulta_a_proveedor(query: str, supplier: str) -> None:
    """Attach a pre-save lookup to the Supplier created from its result."""
    registro = frappe.get_doc("ARCA CUIT Query", query)
    registro.check_permission("read")
    proveedor = frappe.get_doc("Supplier", supplier)
    proveedor.check_permission("write")
    if _canonical_cuit(proveedor.get("tax_id") or "") != _canonical_cuit(
        registro.cuit or ""
    ):
        frappe.throw("El CUIT del proveedor no coincide con la consulta ARCA.")
    if registro.supplier and registro.supplier != supplier:
        frappe.throw("La consulta ya está vinculada a otro proveedor.")
    if registro.customer or registro.sales_invoice:
        frappe.throw("La consulta ya está vinculada a un cliente o factura de venta.")
    frappe.db.set_value(
        "ARCA CUIT Query", registro.name, {"supplier": supplier}
    )


# --- emission: one prepared comprobante, one explicit action ---------------
#
# Nothing here emits on save or on submit: the operator prepares a proposal,
# reads it, confirms the class and presses Emitir. A rejection and an uncertain
# answer are recorded as they are; only Conciliar can move an uncertain one.


def _comprobante_de(sales_invoice: str) -> str | None:
    return frappe.db.get_value(
        "ARCA Comprobante", {"sales_invoice": sales_invoice}, "name"
    )


def _exigir_factura_enviada(factura) -> None:
    if factura.docstatus != 1:
        frappe.throw(
            f"La factura {factura.name} no está enviada. Enviála primero: "
            "el comprobante fiscal se emite sobre la factura definitiva."
        )


def evitar_borrar_factura_con_intento_arca(doc, method=None) -> None:
    """Keep the invoice and its ARCA evidence linked after an emission attempt."""
    comprobantes = frappe.get_all(
        "ARCA Comprobante",
        filters={"sales_invoice": doc.name},
        fields=["name", "estado", "intentos", "cae"],
    )
    for comprobante in comprobantes:
        if (comprobante.get("intentos") or 0) > 0 or comprobante.get("estado") in (
            Estado.SOLICITANDO.value,
            Estado.AUTORIZADO.value,
            Estado.INCIERTO.value,
        ):
            cae = f" con CAE {comprobante.get('cae')}" if comprobante.get("cae") else ""
            frappe.throw(
                f"La factura {doc.name} tiene el comprobante ARCA "
                f"{comprobante.get('name')} en estado "
                f"{comprobante.get('estado')}{cae}. "
                "No se puede borrar: conservá ambos registros para mantener "
                "la trazabilidad fiscal."
            )


def _guardar(doc, **values) -> None:
    """Write engine-owned fields without running the controller.

    ``validate`` is what stops a human from editing a frozen document; the engine
    writes with ``set_value`` because the state, the CAE and the traces are its
    own.
    """
    frappe.db.set_value("ARCA Comprobante", doc.name, values)

    for fieldname, value in values.items():
        doc.set(fieldname, value)


def _replace_invoice_amounts(doc, amounts) -> None:
    """Put the derived amounts on the document, child rows included.

    They are rebuilt from the invoice on every attempt: a mapping the operator
    fixed has to be the one that travels, not a stale copy.
    """
    doc.imp_neto = float(amounts.neto)
    doc.imp_iva = float(amounts.iva)
    doc.imp_trib = float(amounts.trib)
    doc.imp_total = float(amounts.total)

    doc.set("alicuotas", [])
    for alicuota in amounts.alicuotas:
        doc.append(
            "alicuotas",
            {
                "alicuota": alicuota.id,
                "porcentaje": float(rate_for_id(alicuota.id)),
                "base_imp": float(alicuota.base),
                "importe": float(alicuota.importe),
            },
        )


def _faltantes(doc) -> list[str]:
    faltan = []
    if not doc.cbte_tipo:
        faltan.append("el tipo de comprobante")
    if not doc.pto_venta:
        faltan.append("el punto de venta")
    if not doc.condicion_iva_receptor:
        faltan.append("la condición frente al IVA del receptor")
    tipo_documento = str(doc.doc_tipo or "").strip()
    if not tipo_documento:
        faltan.append("el tipo de documento del receptor")
    elif tipo_documento not in {"80", "96", "99"}:
        faltan.append("un tipo de documento del receptor válido (80, 96 o 99)")
    elif tipo_documento != "99":
        numero = str(doc.doc_nro or "").strip()
        if not numero:
            faltan.append("el número de documento del receptor")
        elif (
            not numero.isascii()
            or not numero.isdecimal()
            or int(numero) < 1
        ):
            faltan.append(
                "un número de documento del receptor válido (solo dígitos, mayor que cero)"
            )
    return faltan


def _unir(problema: str, faltantes: list[str]) -> str | None:
    partes = []
    if problema:
        partes.append(problema)
    if faltantes:
        partes.append("Falta completar: " + ", ".join(faltantes) + ".")
    return "\n".join(partes) or None


def _motivo(error) -> str:
    message = getattr(error, "message", None) or str(error)
    code = getattr(error, "code", None)
    detail = getattr(error, "detail", None)
    texto = f"{message} (código {code})" if code else message
    return f"{texto} {detail}".strip() if detail else texto


def _fecha(valor: str | None) -> str | None:
    """ARCA's ``YYYYMMDD`` to the Date field's ``YYYY-MM-DD``."""
    texto = str(valor or "").strip()
    if len(texto) != 8 or not texto.isdigit():
        return None
    return f"{texto[:4]}-{texto[4:6]}-{texto[6:]}"


def _texto(valor: bytes | None) -> str | None:
    return valor.decode("utf-8", "replace") if valor else None


def _solicitud_guardada(doc) -> CaeRequest:
    """Rebuild the expected fiscal payload from the immutable attempt snapshot."""
    try:
        return CaeRequest(
            pto_venta=int(doc.pto_venta),
            cbte_tipo=int(doc.cbte_tipo),
            cbte_desde=int(doc.cbte_nro),
            cbte_fch=fiscal.formatear_fecha(doc.fecha_comprobante),
            doc_tipo=int(doc.doc_tipo),
            doc_nro=int(doc.doc_nro),
            imp_total=float(doc.imp_total),
            imp_neto=float(doc.imp_neto),
            imp_iva=float(doc.imp_iva),
            condicion_iva_receptor=CondicionIvaReceptor(
                int(doc.condicion_iva_receptor)
            ),
            alicuotas=[
                AlicIva(
                    id=int(row.alicuota),
                    base_imp=float(row.base_imp),
                    importe=float(row.importe),
                )
                for row in (doc.alicuotas or ())
            ],
            mon_id="PES",
            mon_cotiz=1.0,
            imp_trib=float(doc.imp_trib or 0),
        )
    except (AttributeError, TypeError, ValueError):
        raise ComprobanteError(
            "No se puede reconstruir el intento guardado para conciliarlo. "
            "No se consultó ARCA; verificá los datos fiscales del comprobante."
        ) from None


def _resultado(doc, mensaje: str) -> dict:
    return {
        "estado": doc.estado,
        "cae": doc.cae,
        "cae_vence": str(doc.cae_vence) if doc.cae_vence else None,
        "numero": doc.cbte_nro,
        "mensaje": mensaje,
    }


def _anotar_problema(doc, message: str) -> None:
    _guardar(doc, problema=message)


# --- the emission lock ------------------------------------------------------
#
# One emission or reconciliation at a time per company. A ``SELECT ... FOR
# UPDATE`` cannot do this:
# the row lock is released by the ``commit`` that stores the assigned number, and
# when no other comprobante exists there is no row to lock at all. So the lock is
# a document named after its key — two processes cannot both create it, and the
# row survives every commit the emission needs.
#
# It is deliberately coarser than "environment + point of sale + type": profiles
# have environment-specific defaults and vouchers can override the point of sale,
# but one company-wide lock also excludes concurrent attempts across environments.
# It refuses instead of queueing, so a worker is never held open waiting for another.


class _BloqueoPerdido(Exception):
    """Another process took the emission lock while this one was still sending.

    Raised from ``guardar_numero`` on purpose: it is not an ARCA answer, so it
    must not be classified as one. The caller puts the document back to draft,
    because nothing was sent.
    """


def _clave_de_bloqueo(company: str) -> str:
    return f"emision:{company}"


def _tomar_bloqueo(company: str, detalle: str) -> str:
    """Take the company's emission lock, or refuse because another one holds it."""
    clave = _clave_de_bloqueo(company)
    token = secrets.token_hex(16)
    ahora = now_datetime()
    vence = ahora + timedelta(seconds=LOCK_TTL_SECONDS)

    # Reclaim a lock whose process died. Only one caller can win this UPDATE,
    # and the read-back proves which one did.
    frappe.db.sql(
        """
        update `tabARCA Bloqueo de Emision`
           set token = %s, vence = %s, detalle = %s
         where clave = %s and vence < %s
        """,
        (token, vence, detalle, clave, ahora),
    )

    if frappe.db.get_value("ARCA Bloqueo de Emision", {"clave": clave}, "token") == token:
        frappe.db.commit()
        return token

    try:
        frappe.get_doc(
            {
                "doctype": "ARCA Bloqueo de Emision",
                "clave": clave,
                "company": company,
                "detalle": detalle,
                "token": token,
                "vence": vence,
            }
        ).insert(ignore_permissions=True)
    except DuplicateEntryError:
        vigente = frappe.db.get_value("ARCA Bloqueo de Emision", {"clave": clave}, "vence")
        frappe.throw(
            "Ya hay una emisión en curso o una conciliación en curso para esta "
            "empresa. Esperá a que termine antes de volver a intentarlo. El bloqueo "
            f"actual vence el {vigente or 'en unos minutos'}."
        )

    frappe.db.commit()
    return token


def _soltar_bloqueo(company: str, token: str) -> None:
    """Release our own lock only: a reclaimed one belongs to another process."""
    frappe.db.sql(
        "delete from `tabARCA Bloqueo de Emision` where clave = %s and token = %s",
        (_clave_de_bloqueo(company), token),
    )
    frappe.db.commit()


def _releer_estado(doc) -> None:
    """Read the stored snapshot again while holding the company-wide lock.

    The document was loaded before the lock was taken, so its state could have
    changed during another emission or reconciliation.
    """
    guardado = (
        frappe.db.get_value(
            "ARCA Comprobante",
            doc.name,
            ["estado", "cbte_nro", "intentos", "environment"],
            as_dict=True,
        )
        or {}
    )

    for campo in ("estado", "cbte_nro", "intentos", "environment"):
        if campo in guardado:
            doc.set(campo, guardado[campo])


def _exigir_bloqueo_propio(company: str, token: str) -> None:
    """Refuse to send when another process took the lock while this one held it.

    The TTL outlives the slowest expected exchange, not an arbitrarily slow
    answer: a process that outlives its own lock must not consume a number a
    concurrent emission may be taking. Nothing is sent, and the attempt has not
    consumed anything, so the document goes back to draft.
    """
    vigente = frappe.db.get_value(
        "ARCA Bloqueo de Emision", {"clave": _clave_de_bloqueo(company)}, "token"
    )

    if vigente != token:
        raise _BloqueoPerdido(
            "Este proceso perdió el bloqueo de emisión mientras leía el número, así "
            "que no se envió nada y el intento no consumió ningún número. Volvé a "
            "presionar Emitir."
        )


def _mismo_ambiente(doc, settings) -> bool:
    return (doc.environment or "") == (settings.environment or "")


def _exigir_ambiente_de_emision(doc, settings) -> None:
    """Refuse to emit into an environment the proposal was not reviewed for.

    The environment is a snapshot: if ``ARCA Settings`` moved, the document the
    operator read no longer describes where the request would go. Only an
    editable document may be refreshed — on a frozen one the stored environment
    *is* the environment the attempt went to, and overwriting it would disarm
    the refusal that protects reconciliation.
    """
    if _mismo_ambiente(doc, settings):
        return

    anterior = doc.environment or "sin definir"

    if doc.estado in EDITABLES:
        mensaje = (
            f"El ambiente de ARCA cambió de {anterior} a {settings.environment}. La "
            "propuesta quedó actualizada y no se envió nada: revisá de nuevo los "
            "datos del comprobante antes de emitir."
        )
        _guardar(doc, environment=settings.environment, problema=mensaje)
        frappe.db.commit()
    else:
        mensaje = (
            f"El ambiente de ARCA cambió de {anterior} a {settings.environment}, y "
            f"este comprobante ya está en {doc.estado}: no se le cambia el ambiente, "
            "porque ese es el ambiente al que se envió. No se envió nada. Para "
            "conciliarlo, apuntá ARCA Settings al ambiente "
            f"{anterior}, o verificá el número directamente en ARCA."
        )

    frappe.throw(mensaje)


def _exigir_ambiente_de_conciliacion(doc, settings) -> None:
    """Refuse to reconcile against an environment that is not the one it went to."""
    if _mismo_ambiente(doc, settings):
        return

    frappe.throw(
        f"Este comprobante se preparó para el ambiente {doc.environment or 'sin definir'}, "
        f"pero ARCA Settings ahora apunta a {settings.environment}. No se consulta: el "
        "intento pudo haber llegado al ambiente anterior, y preguntar en el otro daría "
        "una respuesta falsa. Volvé a configurar ese ambiente en ARCA Settings para "
        "conciliarlo, o verificá el número directamente en ARCA."
    )


def _exigir_emitible(doc) -> None:
    if doc.estado == Estado.INCIERTO.value:
        frappe.throw(
            "El comprobante está en estado Incierto: ARCA puede haber recibido el "
            f"número {doc.cbte_nro or 'informado'}. Usá Conciliar antes de cualquier "
            "intento nuevo; reenviarlo a ciegas puede duplicar el comprobante."
        )

    if doc.estado == Estado.AUTORIZADO.value:
        frappe.throw(
            f"El comprobante ya está autorizado con CAE {doc.cae}. Un comprobante "
            "autorizado no se vuelve a enviar."
        )

    if doc.estado == Estado.SOLICITANDO.value:
        frappe.throw(
            "El comprobante quedó en Solicitando: el intento anterior no terminó. "
            "Usá Conciliar para averiguar si ARCA recibió el comprobante."
        )

    if doc.estado not in EDITABLES:
        frappe.throw(
            f"El comprobante está en estado {doc.estado or 'sin estado'} y no admite "
            "un intento nuevo."
        )

    faltan = _faltantes(doc)
    if faltan:
        frappe.throw("Antes de emitir falta completar: " + ", ".join(faltan) + ".")


def _comprobar_que_no_hay_otro_intento(doc) -> None:
    """One live comprobante per invoice, checked while holding the company lock.

    This is a check, not the lock. ``for update`` here would lock nothing when no
    other comprobante is live, and would not survive the commit that stores the
    assigned number. The exclusion comes from the lock taken before this runs.
    """
    otro = frappe.db.sql(
        """
        select name from `tabARCA Comprobante`
        where sales_invoice = %s and name != %s
          and estado in ('Solicitando', 'Autorizado', 'Incierto')
        """,
        (doc.sales_invoice, doc.name),
    )

    if otro:
        frappe.throw(
            f"La factura {doc.sales_invoice} ya tiene el comprobante {otro[0][0]} en "
            "curso o autorizado. Conciliá ese documento: ARCA puede haber recibido "
            "el comprobante."
        )


@frappe.whitelist()
def preparar_comprobante(sales_invoice: str) -> str:
    """Create or refresh the proposal for one submitted invoice. Never sends.

    A refusal from the pure layer is stored on the document instead of thrown:
    the operator has to be able to open it and read which account or field needs
    attention before pressing Emitir.
    """
    factura = frappe.get_doc("Sales Invoice", sales_invoice)
    factura.check_permission("read")

    _exigir_factura_enviada(factura)

    _check_company_access(factura.company)

    existente = _comprobante_de(factura.name)
    doc = None
    environment = None
    if existente:
        doc = frappe.get_doc("ARCA Comprobante", existente)
        if doc.estado not in EDITABLES:
            doc.check_permission("read")
            return doc.name
        doc.check_permission("write")
        environment = doc.environment
    else:
        frappe.has_permission("ARCA Comprobante", ptype="create", throw=True)

    try:
        settings = _settings(factura.company, environment)
    except AuthError as exc:
        frappe.throw(
            f"{exc.message} Creá un ARCA Settings para {factura.company} "
            "y marcá el ambiente correspondiente como predeterminado."
        )

    if doc is None:
        doc = frappe.new_doc("ARCA Comprobante")
        doc.sales_invoice = factura.name
        doc.company = factura.company
        doc.check_permission("create")

    doc.customer = factura.customer
    doc.environment = settings.environment
    doc.concepto = 1
    doc.pto_venta = doc.pto_venta or settings.pto_venta
    doc.cbte_tipo = doc.cbte_tipo or settings.cbte_tipo

    problema = ""
    try:
        doc.fecha_comprobante = _invoice_date(factura)
    except ComprobanteError as exc:
        doc.fecha_comprobante = factura.get("posting_date")
        problema = str(exc)

    tax_id = factura.get("tax_id")
    if tax_id or not doc.doc_tipo:
        try:
            tipo, numero = receiver_document(tax_id)
        except ComprobanteError as exc:
            problema = str(exc)
        else:
            doc.doc_tipo = str(tipo)
            doc.doc_nro = str(numero)

    try:
        importes = _invoice_amounts(factura, factura.company)
    except ComprobanteError as exc:
        problema = problema or str(exc)
        doc.set("alicuotas", [])
    else:
        _replace_invoice_amounts(doc, importes)

    doc.problema = _unir(problema, _faltantes(doc))
    doc.save()
    return doc.name


@frappe.whitelist()
def emitir_comprobante(name: str) -> dict:
    """Send one prepared comprobante to ARCA, once, on the operator's request.

    Everything that could consume a number happens while the company lock is
    held: re-reading the document, validating it, freezing the attempt, and
    storing the assigned number before the request leaves.
    """
    doc = frappe.get_doc("ARCA Comprobante", name)
    doc.check_permission("write")
    _check_company_access(doc.company)

    try:
        settings = _settings(doc.company, doc.environment)
    except AuthError as exc:
        _anotar_problema(doc, exc.message)
        frappe.throw(exc.message)

    token = _tomar_bloqueo(doc.company, f"Emisión de {doc.sales_invoice}")

    try:
        return _emitir_con_bloqueo(doc, settings, token)
    finally:
        _soltar_bloqueo(doc.company, token)


def _emitir_con_bloqueo(doc, settings, token: str) -> dict:
    _releer_estado(doc)
    _exigir_ambiente_de_emision(doc, settings)
    _exigir_emitible(doc)

    factura = frappe.get_doc("Sales Invoice", doc.sales_invoice)
    _exigir_factura_enviada(factura)

    try:
        doc.fecha_comprobante = _invoice_date(factura)
    except ComprobanteError as exc:
        _anotar_problema(doc, str(exc))
        frappe.throw(str(exc))

    try:
        importes = _invoice_amounts(factura, doc.company)
    except ComprobanteError as exc:
        _anotar_problema(doc, str(exc))
        frappe.throw(str(exc))

    _comprobar_que_no_hay_otro_intento(doc)

    # The document is saved while it is still editable, so the amounts that
    # travel are the ones the operator reviewed. Only then is the attempt
    # frozen: from here on the engine writes with ``set_value``.
    _replace_invoice_amounts(doc, importes)
    doc.problema = None
    doc.save()

    captura: dict[str, bytes] = {}
    numero_asignado = False

    with _private_key_file(settings) as key_path:
        client = _wsfe_client(settings, key_path, doc.company, captura)

        # WSAA first. A ticket failure is provably before the request, so it must
        # neither consume an attempt nor leave the document uncertain.
        try:
            client.ticket()
        except ArcaError as exc:
            motivo = _motivo(exc)
            _anotar_problema(doc, motivo)
            frappe.db.commit()
            frappe.throw(motivo)

        fiscal.exigir_transicion(doc.estado, Estado.SOLICITANDO)
        _guardar(
            doc,
            estado=Estado.SOLICITANDO.value,
            cbte_nro=0,
            resultado=None,
            cae=None,
            cae_vence=None,
            motivo_rechazo=None,
            problema=None,
            solicitud_xml=None,
            respuesta_xml=None,
            intentos=(doc.intentos or 0) + 1,
            solicitado_at=now_datetime(),
            respondido_at=None,
            conciliado_at=None,
        )
        frappe.db.commit()

        def guardar_numero(numero: int) -> None:
            """Store the number before the request leaves, so it can be found again."""
            nonlocal numero_asignado
            _exigir_bloqueo_propio(doc.company, token)
            _guardar(doc, cbte_nro=numero)
            frappe.db.commit()
            numero_asignado = True

        try:
            resultado = fiscal.emitir(
                client,
                pto_venta=int(doc.pto_venta),
                cbte_tipo=int(doc.cbte_tipo),
                solicitud_para=lambda numero: fiscal.construir_solicitud(
                    pto_venta=int(doc.pto_venta),
                    cbte_tipo=int(doc.cbte_tipo),
                    cbte_nro=numero,
                    cbte_fch=doc.fecha_comprobante,
                    condicion_iva_receptor=int(doc.condicion_iva_receptor),
                    importes=importes,
                    doc_tipo=int(doc.doc_tipo or 99),
                    doc_nro=int(doc.doc_nro or 0),
                ),
                al_asignar_numero=guardar_numero,
            )
        except _BloqueoPerdido as exc:
            # The lock went away before the number was stored: nothing was sent
            # and nothing was consumed, so the attempt does not count.
            fiscal.exigir_transicion(Estado.SOLICITANDO, Estado.BORRADOR)
            _guardar(
                doc,
                estado=Estado.BORRADOR.value,
                problema=str(exc),
                solicitado_at=None,
                intentos=max((doc.intentos or 1) - 1, 0),
            )
            frappe.db.commit()
            frappe.throw(str(exc))
        except ArcaError as exc:
            motivo = _motivo(exc)

            if numero_asignado:
                # The request may have reached ARCA. Nothing here can prove it did
                # not, so the attempt is uncertain, never drafted again.
                fiscal.exigir_transicion(Estado.SOLICITANDO, Estado.INCIERTO)
                _guardar(
                    doc,
                    estado=Estado.INCIERTO.value,
                    motivo_rechazo=motivo,
                    respondido_at=now_datetime(),
                )
                frappe.db.commit()
                frappe.throw(
                    "No se pudo confirmar la respuesta de ARCA para el comprobante "
                    f"N.º {doc.cbte_nro}, que ya se había asignado: puede haberlo "
                    f"recibido. ({motivo}) No lo reenvíes: usá Conciliar."
                )

            # The number was never assigned, so nothing was requested: no number
            # was consumed and the attempt does not count.
            fiscal.exigir_transicion(Estado.SOLICITANDO, Estado.BORRADOR)
            _guardar(
                doc,
                estado=Estado.BORRADOR.value,
                problema=motivo,
                solicitado_at=None,
                intentos=max((doc.intentos or 1) - 1, 0),
            )
            frappe.db.commit()
            frappe.throw(motivo)

    solicitud_xml = _texto(captura.get("solicitud"))
    respuesta_xml = _texto(captura.get("respuesta"))

    fiscal.exigir_transicion(doc.estado, resultado.estado)

    if resultado.estado is Estado.AUTORIZADO:
        cae = resultado.cae
        _guardar(
            doc,
            estado=Estado.AUTORIZADO.value,
            cbte_nro=resultado.numero,
            resultado="A",
            cae=cae.cae,
            cae_vence=_fecha(cae.vence),
            respondido_at=now_datetime(),
            motivo_rechazo=None,
            solicitud_xml=solicitud_xml,
            respuesta_xml=respuesta_xml,
        )
        mensaje = (
            f"ARCA autorizó el comprobante N.º {resultado.numero} con CAE "
            f"{cae.cae}, que vence el {cae.vence}."
        )

    elif resultado.estado is Estado.RECHAZADO:
        motivo = _motivo(resultado.error)
        _guardar(
            doc,
            estado=Estado.RECHAZADO.value,
            cbte_nro=resultado.numero,
            resultado="R",
            motivo_rechazo=motivo,
            respondido_at=now_datetime(),
            solicitud_xml=solicitud_xml,
            respuesta_xml=respuesta_xml,
        )
        mensaje = (
            f"ARCA rechazó el comprobante: {motivo} El número no quedó autorizado; "
            "corregí la factura y volvé a intentar cuando el motivo ya no aplique."
        )

    else:
        motivo = _motivo(resultado.error)
        _guardar(
            doc,
            estado=Estado.INCIERTO.value,
            cbte_nro=resultado.numero,
            resultado=None,
            motivo_rechazo=motivo,
            respondido_at=now_datetime(),
            solicitud_xml=solicitud_xml,
            respuesta_xml=respuesta_xml,
        )
        mensaje = (
            "ARCA no confirmó ni rechazó el comprobante: puede haberlo recibido. "
            f"({motivo}) No lo reenvíes: usá Conciliar para consultar el número "
            f"{resultado.numero}."
        )

    frappe.db.commit()
    return _resultado(doc, mensaje)


@frappe.whitelist()
def conciliar_comprobante(name: str) -> dict:
    """Ask ARCA whether that attempt exists, and settle the state from its answer.

    It only reads: no comprobante is sent, and no emission is retried here.
    """
    doc = frappe.get_doc("ARCA Comprobante", name)
    doc.check_permission("write")
    company = _check_company_access(doc.company)

    _exigir_conciliable(doc)
    token = _tomar_bloqueo(company, f"Conciliación de {doc.sales_invoice}")
    try:
        _releer_estado(doc)
        _exigir_conciliable(doc)
        return _conciliar_comprobante_con_bloqueo(doc)
    finally:
        _soltar_bloqueo(company, token)


def _exigir_conciliable(doc) -> None:
    if doc.estado not in CONCILIABLES:
        frappe.throw(
            "Solo se concilia un comprobante en estado Incierto o Solicitando: "
            f"este está en {doc.estado or 'sin estado'}."
        )


def _conciliar_comprobante_con_bloqueo(doc) -> dict:
    try:
        settings = _settings(doc.company, doc.environment)
    except AuthError as exc:
        frappe.throw(exc.message)

    _exigir_ambiente_de_conciliacion(doc, settings)

    if not doc.cbte_nro:
        # The number is stored before the request leaves, so a Solicitando
        # without one proves nothing was sent. It goes back to Borrador without
        # touching the network: there is no number to ask about.
        if doc.estado == Estado.SOLICITANDO.value:
            fiscal.exigir_transicion(doc.estado, Estado.BORRADOR)
            _guardar(
                doc,
                estado=Estado.BORRADOR.value,
                intentos=max((doc.intentos or 1) - 1, 0),
                solicitado_at=None,
                conciliado_at=now_datetime(),
                resultado=None,
                problema=(
                    "El intento se interrumpió antes de que ARCA asignara un número, "
                    "así que no se envió nada. El comprobante vuelve a Borrador: "
                    "revisalo y emití de nuevo."
                ),
            )
            frappe.db.commit()
            return _resultado(
                doc,
                "El intento no llegó a tener número: no se envió nada y el "
                "comprobante vuelve a Borrador. Ya podés emitir de nuevo.",
            )

        frappe.throw(
            "Este comprobante no llegó a tener número, así que no hay nada que "
            "consultar en ARCA. Actualizá sus datos y emití de nuevo."
        )

    captura: dict[str, bytes] = {}

    with _private_key_file(settings) as key_path:
        client = _wsfe_client(settings, key_path, doc.company, captura)
        resultado = fiscal.reconciliar(
            client,
            pto_venta=int(doc.pto_venta),
            cbte_tipo=int(doc.cbte_tipo),
            numero=int(doc.cbte_nro),
            solicitud_esperada=_solicitud_guardada(doc),
        )

    fiscal.exigir_transicion(doc.estado, resultado.estado)
    respuesta_xml = _texto(captura.get("respuesta"))

    if resultado.estado is Estado.AUTORIZADO:
        consulta = resultado.consulta
        _guardar(
            doc,
            estado=Estado.AUTORIZADO.value,
            resultado="A",
            cae=resultado.cae,
            cae_vence=_fecha(consulta.vence if consulta else None),
            conciliado_at=now_datetime(),
            motivo_rechazo=None,
            problema=None,
            respuesta_xml=respuesta_xml,
        )
        mensaje = (
            f"ARCA tiene el comprobante N.º {resultado.numero} autorizado con CAE "
            f"{resultado.cae}. La conciliación cerró el intento."
        )

    else:
        motivo = (
            _motivo(resultado.error)
            if resultado.error
            else "ARCA no devolvió un resultado definitivo para ese número."
        )
        _guardar(
            doc,
            estado=Estado.INCIERTO.value,
            resultado=None,
            cae=None,
            cae_vence=None,
            conciliado_at=now_datetime(),
            motivo_rechazo=None,
            problema=motivo,
            respuesta_xml=respuesta_xml,
        )
        mensaje = (
            f"ARCA sigue sin confirmar el comprobante N.º {resultado.numero}. "
            f"({motivo}) No lo reenvíes: revisá el número en ARCA o esperá y "
            "conciliá otra vez."
        )

    frappe.db.commit()
    return _resultado(doc, mensaje)
