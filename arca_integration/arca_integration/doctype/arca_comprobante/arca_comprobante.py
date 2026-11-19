"""ARCA Comprobante: one emission attempt and the fiscal record of it.

The controller is a shell. Every fiscal rule lives in ``core/``; what needs the
stored document lives here:

- one comprobante per invoice, checked when the document is created;
- a document that already started an attempt is read-only, because its fiscal
  fields were frozen when the request left the process;
- evidence is never deletable once a number may have been consumed.

``estado`` is written by the engine in ``arca_integration.api``, never from
``validate``: the form must not be able to invent a lifecycle.
"""

import frappe
from frappe.model.document import Document

from arca_integration.core.comprobante import ComprobanteError, Estado, clase_de_comprobante

#: States in which the document is evidence: editing or deleting it could
#: describe an emission ARCA has already seen, but differently.
NO_EDITABLES = frozenset(
    {Estado.SOLICITANDO.value, Estado.AUTORIZADO.value, Estado.INCIERTO.value}
)


class ARCAComprobante(Document):
    def before_insert(self):
        existente = frappe.db.get_value(
            "ARCA Comprobante", {"sales_invoice": self.sales_invoice}, "name"
        )

        if existente:
            frappe.throw(
                f"La factura {self.sales_invoice} ya tiene el comprobante ARCA "
                f"{existente}. Abrí ese documento: un segundo comprobante podría "
                "consumir dos números para la misma factura."
            )

    def validate(self):
        if not self.is_new():
            guardado = frappe.db.get_value(
                "ARCA Comprobante", self.name, ["estado", "sales_invoice"], as_dict=True
            ) or {}

            if guardado.get("estado") in NO_EDITABLES:
                frappe.throw(
                    f"El comprobante está en estado {guardado.get('estado')} y ya no "
                    "se puede editar: lo que ARCA recibió tiene que quedar como "
                    "está. Si el resultado es incierto, usá Conciliar."
                )

            if guardado.get("sales_invoice") not in (None, self.sales_invoice):
                frappe.throw(
                    "La factura de un comprobante no se puede cambiar. Prepará el "
                    "comprobante desde la factura correcta."
                )

        self._fijar_clase()
        self._normalizar_documento()

    def on_trash(self):
        if (self.intentos or 0) > 0:
            frappe.throw(
                "Un comprobante con intentos de emisión no se puede borrar: es el "
                "registro de lo que ARCA recibió para esa factura."
            )

    def _fijar_clase(self):
        if not self.cbte_tipo:
            self.cbte_clase = None
            return

        try:
            self.cbte_clase = clase_de_comprobante(self.cbte_tipo)
        except ComprobanteError as error:
            frappe.throw(str(error))

    def _normalizar_documento(self):
        if str(self.doc_tipo or "").strip() == "99":
            self.doc_nro = "0"
