"""One IVA line of an emission, as it was sent to ARCA.

The row is written by the engine from ``AlicuotaImporte``; the form never edits
it. The validation lives in ``core/wsfe/alicuotas.py`` so it can be exercised
without a bench, and this controller is only the adapter that turns a refund
into a form error.
"""

import frappe
from frappe.model.document import Document

from arca_integration.core.comprobante import ComprobanteError
from arca_integration.core.wsfe.alicuotas import validate_iva_row


class ARCAAlicuotaIVA(Document):
    def validate(self):
        try:
            validate_iva_row(
                self.alicuota, self.porcentaje, self.base_imp, self.importe
            )
        except ComprobanteError as error:
            frappe.throw(str(error))
