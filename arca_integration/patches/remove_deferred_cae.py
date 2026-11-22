import frappe


def execute():
	"""The CAE is always requested on submit: drop the field of the removed After Submit option."""
	frappe.delete_doc_if_exists("Custom Field", "Sales Invoice-arca_cae_deadline")
