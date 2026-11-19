"""Says whether a revenue account is taxable.

The rule lives in ``core/mapeo.py`` so it can be exercised without a bench; this
controller only reads the account's ERPNext type and turns a refusal into a form
error.
"""

import frappe
from frappe.model.document import Document

from arca_integration.core.comprobante import ComprobanteError
from arca_integration.core.mapeo import validate_income_mapping


class ARCAMapeodeCuentadeIngreso(Document):
    def validate(self):
        root_type = frappe.db.get_value("Account", self.account, "root_type")
        try:
            validate_income_mapping(self.account, self.naturaleza, root_type)
        except ComprobanteError as error:
            frappe.throw(str(error))
