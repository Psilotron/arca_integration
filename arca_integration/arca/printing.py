"""What the "Factura ARCA" print format shows (RG 1415 Anexo II, RG 4291, RG 5614).

`arca_print_data` rebuilds the voucher with the builder, keeping the letter ARCA
already gave it, and adds what only the printed copy needs: issuer and receiver
data, legends, the CAE and the QR. The template only lays it out.
"""

import base64

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import getdate

from arca_integration.arca.invoice import applies, kind_of, review
from arca_integration.services import qr
from arca_integration.services.endpoints import Environment
from arca_integration.services.mtxca.builder import PESOS
from arca_integration.services.mtxca.voucher import Kind, Letter

FINAL_CONSUMER = "5"  # ARCA receiver VAT condition
TITLES = {
	Kind.INVOICE: "FACTURA",
	Kind.DEBIT_NOTE: "NOTA DE DÉBITO",
	Kind.CREDIT_NOTE: "NOTA DE CRÉDITO",
}


def arca_print_data(doc) -> dict:
	"""Everything the print format shows for Sales Invoice `doc`."""
	if not applies(doc):
		return {"applies": False}
	settings = frappe.get_cached_doc("ARCA Company Settings", doc.company)
	voucher = review(doc, check_rate=False).voucher
	if voucher is None:
		return {"applies": True, "voucher": None}

	authorized = bool(doc.get("arca_cae"))
	number = doc.get("arca_voucher_number") if authorized else None
	rates = dict(frappe.get_all("ARCA VAT Condition", fields=["name", "rate"], as_list=True))
	vat_names = dict(frappe.get_all("ARCA VAT Condition", fields=["name", "description"], as_list=True))
	letter_a = voucher.letter == Letter.A

	return {
		"applies": True,
		"voucher": True,
		"authorized": authorized,
		"homologation": _environment(doc, settings) == Environment.HOMOLOGATION,
		"letter": voucher.letter,
		"code": f"{voucher.voucher_type:02d}",
		"title": TITLES[kind_of(doc)],
		"point_of_sale": f"{voucher.point_of_sale:05d}",
		"number": f"{number:08d}" if number else None,
		"issue_date": voucher.issue_date,
		"issuer": _issuer(doc, settings),
		"receiver": _receiver(doc, voucher),
		"sale_condition": doc.payment_terms_template or _("Cash Sale"),
		"concept": voucher.concept,
		"service_start": voucher.service_start,
		"service_end": voucher.service_end,
		"payment_due": voucher.payment_due,
		"currency": voucher.currency,
		"foreign_currency": voucher.currency != PESOS,
		"exchange_rate": voucher.exchange_rate,
		"show_vat": letter_a,
		"lines": [
			{
				"code": item.code,
				"description": item.description,
				"quantity": item.quantity,
				"uom": row.uom,
				"unit_price": item.unit_price,
				"vat_rate": rates.get(item.vat_condition) if letter_a else None,
				"vat_amount": item.vat_amount,
				"amount": item.amount,
			}
			for item, row in zip(voucher.items, doc.items, strict=False)
		],
		"vat_subtotals": [
			{"name": vat_names.get(s.vat_condition, s.vat_condition), "amount": s.amount}
			for s in voucher.vat_subtotals
		]
		if letter_a
		else [],
		"other_taxes": [{"name": t.description, "amount": t.amount} for t in voucher.other_taxes],
		"taxed": voucher.taxed,
		"untaxed": voucher.untaxed,
		"exempt": voucher.exempt,
		"subtotal": voucher.subtotal if letter_a else voucher.total - voucher.other_taxes_total,
		"total": voucher.total,
		# Ley 27.743: a B voucher to a final consumer states the VAT it contains.
		"transparency": {"vat": voucher.vat_total, "other": 0}
		if not letter_a and voucher.receiver_vat_condition == FINAL_CONSUMER
		else None,
		"associated": [
			{
				"name": frappe.db.get_value("ARCA Voucher Type", str(a.voucher_type), "description"),
				"number": f"{a.point_of_sale:05d}-{a.number:08d}",
				"issue_date": a.issue_date,
			}
			for a in voucher.associated
		],
		"cae": doc.get("arca_cae"),
		"cae_due_date": doc.get("arca_cae_due_date"),
		"qr": _qr(doc, settings, voucher) if authorized else None,
	}


def _environment(doc, settings) -> str:
	"""The environment that authorized the voucher; the current one if it has no CAE yet."""
	if doc.get("arca_cae"):
		logged = frappe.db.get_value(
			"ARCA Request Log",
			{"sales_invoice": doc.name, "cae": doc.arca_cae},
			"environment",
		)
		if logged:
			return logged
	return settings.environment


def _issuer(doc, settings) -> dict:
	cuit = settings.cuit or ""
	return {
		"name": frappe.db.get_value("Company", doc.company, "company_name") or doc.company,
		"cuit": f"{cuit[:2]}-{cuit[2:10]}-{cuit[10:]}" if len(cuit) == 11 else cuit,
		"vat_condition": _("IVA Responsable Inscripto"),
		"gross_income": settings.gross_income_number or "",
		"activity_start": settings.activity_start_date,
		"address": get_address_display(settings.commercial_address) if settings.commercial_address else "",
	}


def _receiver(doc, voucher) -> dict:
	return {
		"name": doc.customer_name,
		"document_type": voucher.document_type
		and frappe.db.get_value("ARCA Document Type", voucher.document_type, "description"),
		"document_number": voucher.document_number,
		"vat_condition": frappe.db.get_value(
			"ARCA Receiver VAT Condition", voucher.receiver_vat_condition, "description"
		),
		"address": doc.address_display or "",
	}


def _qr(doc, settings, voucher) -> str:
	"""The QR as an image the PDF renders (SVG in a data URI)."""
	point_of_sale = frappe.db.get_value("ARCA Point of Sale", doc.arca_point_of_sale, "number")
	content = qr.text(
		qr.payload(
			issue_date=getdate(doc.posting_date),
			cuit=settings.cuit,
			point_of_sale=int(point_of_sale or voucher.point_of_sale),
			voucher_type=int(doc.arca_voucher_type),
			number=int(doc.arca_voucher_number),
			total=voucher.total,
			currency=voucher.currency,
			exchange_rate=voucher.exchange_rate,
			document_type=voucher.document_type,
			document_number=voucher.document_number,
			cae=doc.arca_cae,
		)
	)
	return "data:image/svg+xml;base64," + base64.b64encode(qr.svg(content).encode()).decode()
