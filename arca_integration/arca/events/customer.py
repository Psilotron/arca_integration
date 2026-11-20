"""Customer document events."""

import frappe
from frappe import _

from arca_integration.services.credentials import is_valid_cuit, normalize_cuit

# Document types whose number is a CUIT-like 11-digit code with a check digit:
# 80 CUIT, 86 CUIL, 87 CDI.
CUIT_DOCUMENT_TYPES = {"80", "86", "87"}
CUIT = "80"


def validate(doc, method=None):
	if not doc.tax_id:
		return

	if not doc.arca_document_type and is_valid_cuit(doc.tax_id):
		doc.arca_document_type = CUIT

	if doc.arca_document_type in CUIT_DOCUMENT_TYPES:
		doc.tax_id = normalize_cuit(doc.tax_id)
		if not is_valid_cuit(doc.tax_id):
			frappe.throw(
				_("Tax ID {0} is not a valid number for document type {1}.").format(
					doc.tax_id, doc.arca_document_type
				)
			)
