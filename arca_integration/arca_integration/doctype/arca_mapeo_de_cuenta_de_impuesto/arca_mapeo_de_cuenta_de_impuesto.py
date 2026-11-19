"""Says what a taxes row is: IVA, a perception, or something else.

The rate is deliberately not stored here. It is read from the row itself and
matched against the measured catalog, so the mapping can never disagree with the
tax it describes.
"""

import frappe
from frappe.model.document import Document

from arca_integration.core.comprobante import ComprobanteError
from arca_integration.core.mapeo import validate_tax_mapping


class ARCAMapeodeCuentadeImpuesto(Document):
    def validate(self):
        try:
            validate_tax_mapping(self.account, self.naturaleza)
        except ComprobanteError as error:
            frappe.throw(str(error))
