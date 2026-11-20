"""Frappe adapter between Sales Invoice and the pure MTXCA builder.

`read_draft` turns the invoice and the ARCA catalogs into plain data, `review`
runs the builder, and `explain` turns each issue into a message with the action
that fixes it. No fiscal rule lives here.
"""

import re
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, getdate, strip_html

from arca_integration.services.credentials import normalize_cuit
from arca_integration.services.endpoints import Environment
from arca_integration.services.mtxca.builder import Draft, DraftLine, DraftTax, Receiver, Review, Rules
from arca_integration.services.mtxca.builder import review as build_review
from arca_integration.services.mtxca.validation import Check, Issue
from arca_integration.services.mtxca.voucher import EXEMPT, UNTAXED, Concept, Voucher

CONCEPTS = {
	"Products": Concept.PRODUCTS,
	"Services": Concept.SERVICES,
	"Products and Services": Concept.PRODUCTS_AND_SERVICES,
}
CONCEPT_LABELS = {concept: label for label, concept in CONCEPTS.items()}

# Barcode types that carry a GTIN, and the lengths ARCA accepts (GTIN-8, 12 and 13).
GTIN_BARCODE_TYPES = {"EAN", "EAN-13", "EAN-8", "UPC-A", "UPC", "GTIN", "JAN"}
_GTIN_RE = re.compile(r"^(\d{8}|\d{12,13})$")
# Generic code from the manual's examples, accepted in homologation.
HOMOLOGATION_GTIN = "7790001001139"
MAX_DESCRIPTION = 4000


def applies(doc) -> bool:
	"""Invoices of companies set up for ARCA. Credit and debit notes come in a later version."""
	return (
		doc.doctype == "Sales Invoice"
		and not doc.is_return
		and not doc.get("is_debit_note")
		and bool(frappe.db.exists("ARCA Company Settings", doc.company))
	)


def review(doc) -> Review:
	settings = frappe.get_cached_doc("ARCA Company Settings", doc.company)
	return build_review(read_draft(doc, settings), read_rules(settings))


# ---- Sales Invoice -> Draft ----


def read_draft(doc, settings) -> Draft:
	customer = frappe.db.get_value(
		"Customer",
		doc.customer,
		["customer_name", "tax_id", "arca_document_type", "arca_receiver_vat_condition"],
		as_dict=True,
	)
	invoice_vat_rate = _invoice_vat_rate(doc)
	point_of_sale = None
	if settings.default_point_of_sale:
		point_of_sale = frappe.db.get_value("ARCA Point of Sale", settings.default_point_of_sale, "number")

	return Draft(
		issue_date=getdate(doc.posting_date),
		due_date=getdate(doc.due_date) if doc.due_date else None,
		receiver=Receiver(
			name=customer.customer_name,
			vat_condition=customer.arca_receiver_vat_condition or "",
			document_type=customer.arca_document_type or "",
			document_number=normalize_cuit(doc.tax_id or customer.tax_id or ""),
		),
		lines=tuple(_read_line(row, settings, invoice_vat_rate) for row in doc.items),
		taxes=tuple(_read_tax(row, doc) for row in doc.taxes),
		grand_total=_decimal(doc.grand_total),
		currency=frappe.db.get_value("Currency", doc.currency, "arca_currency") or "",
		exchange_rate=_decimal(doc.conversion_rate or 1, 6),
		point_of_sale=point_of_sale,
		concept=CONCEPTS.get(doc.arca_concept or ""),
		service_start=getdate(doc.arca_service_start_date) if doc.arca_service_start_date else None,
		service_end=getdate(doc.arca_service_end_date) if doc.arca_service_end_date else None,
	)


def _read_line(row, settings, invoice_vat_rate) -> DraftLine:
	vat_condition = _item_vat_condition(row.item_tax_template) or settings.default_vat_condition
	if not vat_condition and invoice_vat_rate is not None:
		vat_condition = _vat_condition_for_rate(invoice_vat_rate)
	description = strip_html(row.description or "").strip() or row.item_name
	return DraftLine(
		row=row.idx,
		description=description[:MAX_DESCRIPTION],
		item_code=row.item_code or "",
		quantity=_decimal(row.qty, 6),
		net_amount=_decimal(row.net_amount),
		unit=frappe.db.get_value("UOM", row.uom, "arca_unit_of_measure") or "",
		vat_condition=vat_condition or "",
		gtin=_gtin(row, settings),
		is_stock_item=bool(frappe.db.get_value("Item", row.item_code, "is_stock_item")),
	)


def _item_vat_condition(template: str | None) -> str | None:
	"""The template's ARCA VAT condition, or the one matching the rate of its VAT account."""
	if not template:
		return None
	if condition := frappe.db.get_value("Item Tax Template", template, "arca_vat_condition"):
		return condition
	rates = {
		flt(rate, 3)
		for account, rate in frappe.get_all(
			"Item Tax Template Detail",
			filters={"parent": template, "parenttype": "Item Tax Template"},
			fields=["tax_type", "tax_rate"],
			as_list=True,
		)
		if frappe.db.get_value("Account", account, "arca_is_vat")
	}
	return _vat_condition_for_rate(rates.pop()) if len(rates) == 1 else None


def _invoice_vat_rate(doc) -> float | None:
	"""Rate of the invoice's only VAT row on net total, if it has exactly one."""
	rows = [
		row
		for row in doc.taxes
		if row.charge_type == "On Net Total"
		and frappe.db.get_value("Account", row.account_head, "arca_is_vat")
	]
	return flt(rows[0].rate, 3) if len(rows) == 1 else None


def _vat_condition_for_rate(rate: float) -> str | None:
	"""VAT condition with this rate; 0 % is ambiguous (untaxed, exempt or 0 %), so it has none."""
	if not rate:
		return None
	for code, condition_rate in frappe.get_all("ARCA VAT Condition", fields=["name", "rate"], as_list=True):
		if code not in (UNTAXED, EXEMPT) and flt(condition_rate, 3) == flt(rate, 3):
			return code
	return None


def _gtin(row, settings) -> str:
	if row.barcode and _GTIN_RE.match(row.barcode):
		return row.barcode
	for barcode, barcode_type in frappe.get_all(
		"Item Barcode",
		filters={"parent": row.item_code, "parenttype": "Item"},
		fields=["barcode", "barcode_type"],
		order_by="idx asc",
		as_list=True,
	):
		if barcode_type in GTIN_BARCODE_TYPES and _GTIN_RE.match(barcode or ""):
			return barcode
	if settings.default_gtin:
		return settings.default_gtin
	return HOMOLOGATION_GTIN if settings.environment == Environment.HOMOLOGATION else ""


def _read_tax(row, doc) -> DraftTax:
	account = frappe.db.get_value("Account", row.account_head, ["arca_is_vat", "arca_tax_type"], as_dict=True)
	return DraftTax(
		row=row.idx,
		account=row.account_head,
		description=row.description or row.account_head,
		amount=_decimal(row.tax_amount_after_discount_amount),
		base=_decimal(row.net_amount or doc.net_total),
		is_vat=bool(account and account.arca_is_vat),
		tax_type=(account and account.arca_tax_type) or "",
	)


def read_rules(settings) -> Rules:
	vat_rates = {}
	for code, rate in frappe.get_all("ARCA VAT Condition", fields=["name", "rate"], as_list=True):
		vat_rates[code] = None if code in (UNTAXED, EXEMPT) else _decimal(rate, 3)

	receiver_voucher_types: dict[str, list[int]] = {}
	for condition, voucher_type in frappe.get_all(
		"ARCA Receiver VAT Condition Voucher Type",
		fields=["parent", "voucher_type"],
		as_list=True,
	):
		receiver_voucher_types.setdefault(condition, []).append(int(voucher_type))

	return Rules(
		issuer_cuit=settings.cuit,
		vat_rates=vat_rates,
		receiver_voucher_types={code: tuple(types) for code, types in receiver_voucher_types.items()},
		anonymous_limit=_decimal(frappe.get_cached_doc("ARCA Settings").anonymous_consumer_limit),
		today=getdate(),
	)


def _decimal(value, places: int = 2) -> Decimal:
	return Decimal(str(flt(value, places)))


# ---- Issue -> message and action ----


def explain(issue: Issue, doc) -> dict:
	"""What happened and how to fix it, in the user's language."""
	values = issue.values
	row = doc.items[issue.row - 1] if issue.row and issue.check.name.startswith("LINE_") else None
	tax = doc.taxes[issue.row - 1] if issue.row and issue.check == Check.TAX_ACCOUNT_UNMAPPED else None
	item = row.item_code if row else ""

	def action(label, doctype, name=None, fieldname=None):
		return {"label": label, "doctype": doctype, "name": name, "fieldname": fieldname}

	messages = {
		Check.NO_POINT_OF_SALE: lambda: (
			_("The company has no ARCA point of sale."),
			action(_("Verify Connection"), "ARCA Company Settings", doc.company),
		),
		Check.RECEIVER_VAT_CONDITION_MISSING: lambda: (
			_("Customer {0} has no ARCA VAT condition.").format(doc.customer_name),
			action(_("Open Customer"), "Customer", doc.customer, "arca_receiver_vat_condition"),
		),
		Check.RECEIVER_VAT_CONDITION_NOT_ALLOWED: lambda: (
			_("ARCA does not allow A or B invoices for the VAT condition of {0}.").format(doc.customer_name),
			action(_("Open Customer"), "Customer", doc.customer, "arca_receiver_vat_condition"),
		),
		Check.RECEIVER_CUIT_REQUIRED: lambda: (
			_("An A invoice needs the customer's CUIT. Set the document type to CUIT (80)."),
			action(_("Open Customer"), "Customer", doc.customer, "arca_document_type"),
		),
		Check.RECEIVER_DOCUMENT_INCOMPLETE: lambda: (
			_("Customer {0} needs both the document type and its number (Tax ID).").format(doc.customer_name),
			action(_("Open Customer"), "Customer", doc.customer, "tax_id"),
		),
		Check.RECEIVER_DOCUMENT_REQUIRED: lambda: (
			_("From {0} the customer must be identified with CUIT, CUIL or DNI.").format(
				fmt_money(flt(values[0] if values else 0), currency="ARS")
			),
			action(_("Open Customer"), "Customer", doc.customer, "tax_id"),
		),
		Check.RECEIVER_IS_ISSUER: lambda: (
			_("The customer's CUIT is the company's own CUIT."),
			action(_("Open Customer"), "Customer", doc.customer, "tax_id"),
		),
		Check.CURRENCY_MISSING: lambda: (
			_("Currency {0} has no ARCA currency.").format(doc.currency),
			action(_("Open Currency"), "Currency", doc.currency, "arca_currency"),
		),
		Check.LINE_VAT_CONDITION_MISSING: lambda: (
			_("Row {0} ({1}): no ARCA VAT condition.").format(issue.row, item),
			action(
				_("Open Item Tax Template"), "Item Tax Template", row.item_tax_template, "arca_vat_condition"
			)
			if row and row.item_tax_template
			else action(
				_("Set Default VAT Condition"), "ARCA Company Settings", doc.company, "default_vat_condition"
			),
		),
		Check.LINE_UNIT_MISSING: lambda: (
			_("Row {0} ({1}): unit {2} has no ARCA unit of measure.").format(
				issue.row, item, row.uom if row else ""
			),
			action(_("Open UOM"), "UOM", row.uom if row else None, "arca_unit_of_measure"),
		),
		Check.LINE_GTIN_MISSING: lambda: (
			_("Row {0} ({1}): no product code (GTIN). Add an EAN or GTIN barcode to the item.").format(
				issue.row, item
			),
			action(_("Open Item"), "Item", item, "barcodes"),
		),
		Check.LINE_NOT_POSITIVE: lambda: (
			_(
				"Row {0} ({1}): quantity and amount must be positive. Use a credit note to return items."
			).format(issue.row, item),
			None,
		),
		Check.TAX_ACCOUNT_UNMAPPED: lambda: (
			_("Tax row {0}: account {1} is neither marked as VAT nor has an ARCA tax type.").format(
				issue.row, tax.account_head if tax else (values[0] if values else "")
			),
			action(_("Open Account"), "Account", tax.account_head if tax else None, "arca_tax_type"),
		),
		Check.ISSUE_DATE_OUT_OF_RANGE: lambda: (
			_(
				"ARCA accepts this invoice dated up to {0} days before or after today, within the current month for products."
			).format(values[0] if values else ""),
			action(_("Change Date"), "Sales Invoice", doc.name, "posting_date"),
		),
		Check.SERVICE_DATES_ORDER: lambda: (
			_("The service start date is after its end date."),
			action(_("Change Dates"), "Sales Invoice", doc.name, "arca_service_start_date"),
		),
		Check.PAYMENT_DUE_BEFORE_ISSUE: lambda: (
			_("The payment due date is before the invoice date."),
			action(_("Change Date"), "Sales Invoice", doc.name, "due_date"),
		),
		Check.VAT_MISMATCH: lambda: (
			_(
				"VAT for ARCA is {0}, but the invoice taxes add up to {1}. Check the items' tax templates."
			).format(*(_money(value, doc) for value in values)),
			None,
		),
		Check.TOTAL_MISMATCH: lambda: (
			_("The total for ARCA is {0}, but the invoice total is {1}.").format(
				*(_money(value, doc) for value in values)
			),
			None,
		),
	}
	message, fix = messages[issue.check]()
	return {"check": str(issue.check), "row": issue.row, "message": message, "action": fix}


def _money(value, doc) -> str:
	return fmt_money(flt(value), currency=doc.currency)


# ---- Review -> what the form shows ----


def summary(result: Review, doc) -> dict:
	settings = frappe.get_cached_doc("ARCA Company Settings", doc.company)
	data = {
		"environment": settings.environment,
		"ready": result.ready,
		"issues": [explain(issue, doc) for issue in result.issues],
		"concept": result.concept and _(CONCEPT_LABELS[result.concept]),
		"voucher": None,
	}
	if result.voucher:
		data["voucher"] = _voucher_data(result.voucher, doc)
	return data


def _voucher_data(voucher: Voucher, doc) -> dict:
	names = {
		"voucher_type": frappe.db.get_value("ARCA Voucher Type", str(voucher.voucher_type), "description"),
		"receiver_vat_condition": frappe.db.get_value(
			"ARCA Receiver VAT Condition", voucher.receiver_vat_condition, "description"
		),
		"document_type": voucher.document_type
		and frappe.db.get_value("ARCA Document Type", voucher.document_type, "description"),
	}
	vat_names = dict(frappe.get_all("ARCA VAT Condition", fields=["name", "description"], as_list=True))
	return {
		"voucher_type": voucher.voucher_type,
		"voucher_type_name": names["voucher_type"],
		"letter": voucher.letter,
		"point_of_sale": voucher.point_of_sale,
		"issue_date": voucher.issue_date,
		"receiver": doc.customer_name,
		"document_type": names["document_type"] or "",
		"document_number": voucher.document_number,
		"receiver_vat_condition": names["receiver_vat_condition"],
		"concept": _(CONCEPT_LABELS[voucher.concept]),
		"service_start": voucher.service_start,
		"service_end": voucher.service_end,
		"payment_due": voucher.payment_due,
		"currency": doc.currency,
		"exchange_rate": voucher.exchange_rate,
		"vat_subtotals": [
			{"name": vat_names.get(subtotal.vat_condition, subtotal.vat_condition), "amount": subtotal.amount}
			for subtotal in voucher.vat_subtotals
		],
		"other_taxes": [{"name": tax.description, "amount": tax.amount} for tax in voucher.other_taxes],
		"taxed": voucher.taxed,
		"untaxed": voucher.untaxed,
		"exempt": voucher.exempt,
		"other_taxes_total": voucher.other_taxes_total,
		"total": voucher.total,
		"grand_total": doc.grand_total,
	}


@frappe.whitelist()
def get_review(name: str) -> dict:
	"""What ARCA would receive for draft Sales Invoice `name`, and what blocks it."""
	doc = frappe.get_doc("Sales Invoice", name)
	doc.check_permission("read")
	if not applies(doc):
		return {"applies": False}
	return {"applies": True, **summary(review(doc), doc)}


# ---- doc_events ----


def before_submit(doc, method=None):
	"""Block submitting an invoice that ARCA would reject."""
	if not applies(doc):
		return
	result = review(doc)
	if result.ready:
		return
	messages = [explain(issue, doc)["message"] for issue in result.issues]
	frappe.throw(
		"<ul>{}</ul>".format("".join(f"<li>{frappe.utils.escape_html(m)}</li>" for m in messages)),
		title=_("ARCA would reject this invoice"),
	)
