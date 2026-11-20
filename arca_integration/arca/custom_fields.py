"""Custom fields that this app adds to Frappe and ERPNext DocTypes.

This is the only place where they are defined. They are created or updated
after install and after every migrate, and removed before uninstall, so
ERPNext forms never keep links to DocTypes that no longer exist.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def N_(message: str) -> str:
	"""Mark a string for extraction into main.pot without translating it here.

	Labels are stored in English and translated by the UI at display time.
	"""
	return message


MODULE = "ARCA"
PREFIX = "arca_"

# Select options, listed so they are extracted into main.pot.
CONCEPT_OPTIONS = [N_("Products"), N_("Services"), N_("Products and Services")]
STATUS_OPTIONS = [N_("Pending"), N_("Authorized"), N_("Rejected"), N_("Uncertain")]

# Read-only fields on Sales Invoice that only voucher emission writes. They are
# never copied, so a duplicated or amended invoice starts without a CAE.
_EMISSION = {"read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1}


def get_custom_fields() -> dict[str, list[dict]]:
	return {
		"Item Tax Template": [
			{
				"fieldname": "arca_vat_condition",
				"fieldtype": "Link",
				"label": N_("ARCA VAT Condition"),
				"options": "ARCA VAT Condition",
				"insert_after": "company",
				"description": N_("VAT condition reported to ARCA for items that use this template."),
			},
		],
		"Account": [
			{
				"fieldname": "arca_is_vat",
				"fieldtype": "Check",
				"label": N_("Is ARCA VAT Account"),
				"insert_after": "tax_rate",
				"depends_on": "eval:doc.account_type=='Tax'",
				"description": N_(
					"Output VAT account. Its amounts are reported as VAT subtotals, not as other taxes."
				),
			},
			{
				"fieldname": "arca_tax_type",
				"fieldtype": "Link",
				"label": N_("ARCA Tax Type"),
				"options": "ARCA Tax Type",
				"insert_after": "arca_is_vat",
				"depends_on": "eval:doc.account_type=='Tax' && !doc.arca_is_vat",
				"description": N_("Tax reported to ARCA as another tax (perceptions, internal taxes, etc.)."),
			},
		],
		"Customer": [
			{
				"fieldname": "arca_document_type",
				"fieldtype": "Link",
				"label": N_("ARCA Document Type"),
				"options": "ARCA Document Type",
				"insert_after": "tax_id",
				"description": N_("Type of the identity document in Tax ID (80 = CUIT, 96 = DNI)."),
			},
			{
				"fieldname": "arca_receiver_vat_condition",
				"fieldtype": "Link",
				"label": N_("ARCA VAT Condition"),
				"options": "ARCA Receiver VAT Condition",
				"insert_after": "arca_document_type",
			},
		],
		"UOM": [
			{
				"fieldname": "arca_unit_of_measure",
				"fieldtype": "Link",
				"label": N_("ARCA Unit of Measure"),
				"options": "ARCA Unit of Measure",
				"insert_after": "uom_name",
			},
		],
		"Currency": [
			{
				"fieldname": "arca_currency",
				"fieldtype": "Link",
				"label": N_("ARCA Currency"),
				"options": "ARCA Currency",
				"insert_after": "currency_name",
			},
		],
		"Sales Invoice": [
			{
				"fieldname": "arca_tab",
				"fieldtype": "Tab Break",
				"label": N_("ARCA"),
				"insert_after": "is_discounted",
			},
			{
				"fieldname": "arca_concept",
				"fieldtype": "Select",
				"label": N_("ARCA Concept"),
				"options": "\n".join(["", *CONCEPT_OPTIONS]),
				"insert_after": "arca_tab",
				"description": N_("Leave empty to derive it from the items."),
			},
			{
				"fieldname": "arca_service_start_date",
				"fieldtype": "Date",
				"label": N_("Service Start Date"),
				"insert_after": "arca_concept",
				"depends_on": "eval:doc.arca_concept!='Products'",
			},
			{
				"fieldname": "arca_service_end_date",
				"fieldtype": "Date",
				"label": N_("Service End Date"),
				"insert_after": "arca_service_start_date",
				"depends_on": "eval:doc.arca_concept!='Products'",
			},
			{
				"fieldname": "arca_column_break",
				"fieldtype": "Column Break",
				"insert_after": "arca_service_end_date",
			},
			{
				"fieldname": "arca_status",
				"fieldtype": "Select",
				"label": N_("ARCA Status"),
				"options": "\n".join(["", *STATUS_OPTIONS]),
				"insert_after": "arca_column_break",
				"in_standard_filter": 1,
				**_EMISSION,
			},
			{
				"fieldname": "arca_voucher_type",
				"fieldtype": "Link",
				"label": N_("ARCA Voucher Type"),
				"options": "ARCA Voucher Type",
				"insert_after": "arca_status",
				**_EMISSION,
			},
			{
				"fieldname": "arca_point_of_sale",
				"fieldtype": "Link",
				"label": N_("ARCA Point of Sale"),
				"options": "ARCA Point of Sale",
				"insert_after": "arca_voucher_type",
				**_EMISSION,
			},
			{
				"fieldname": "arca_voucher_number",
				"fieldtype": "Int",
				"label": N_("ARCA Voucher Number"),
				"insert_after": "arca_point_of_sale",
				**_EMISSION,
			},
			{
				"fieldname": "arca_cae",
				"fieldtype": "Data",
				"label": N_("CAE"),
				"insert_after": "arca_voucher_number",
				"search_index": 1,
				**_EMISSION,
			},
			{
				"fieldname": "arca_cae_due_date",
				"fieldtype": "Date",
				"label": N_("CAE Due Date"),
				"insert_after": "arca_cae",
				**_EMISSION,
			},
		],
	}


def _with_module(custom_fields: dict[str, list[dict]]) -> dict[str, list[dict]]:
	return {
		doctype: [{**field, "module": MODULE} for field in fields]
		for doctype, fields in custom_fields.items()
	}


def create_arca_custom_fields() -> None:
	create_custom_fields(_with_module(get_custom_fields()), update=True)


def delete_arca_custom_fields() -> None:
	for doctype, fields in get_custom_fields().items():
		for field in fields:
			frappe.delete_doc_if_exists("Custom Field", f"{doctype}-{field['fieldname']}")
		frappe.clear_cache(doctype=doctype)
