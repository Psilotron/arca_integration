"""Purchase invoices: ARCA must know the supplier's voucher before it is submitted (WSCDC).

The voucher's data comes from its QR, or is typed in the ARCA tab. "Verify in
ARCA" asks WSCDC; submitting verifies again if the data changed, and is
blocked unless ARCA confirms the voucher. Suppliers marked "Does Not Issue
ARCA Vouchers" (foreign suppliers, for example) are left alone.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from arca_integration.arca.connection import problem_message
from arca_integration.arca.mtxca import get_wscdc_client
from arca_integration.services import qr
from arca_integration.services.credentials import is_valid_cuit, normalize_cuit
from arca_integration.services.diagnosis import classify
from arca_integration.services.endpoints import WSAA_SERVICE_IDS, Service
from arca_integration.services.errors import ArcaError
from arca_integration.services.wscdc.client import CUIT_DOCUMENT, ReceivedVoucher, Verification
from arca_integration.services.wscdc.messages import EXPLANATIONS

VERIFIED, REJECTED = "Verified", "Rejected"
LOG = "ARCA Request Log"
EXEMPTING_ROLE = "Accounts Manager"
# What ARCA checks. If any of it changes, the invoice must be verified again.
VERIFIED_FIELDS = (
	"company",
	"tax_id",
	"bill_date",
	"grand_total",
	"arca_authorization_mode",
	"arca_authorization_code",
	"arca_voucher_type",
	"arca_point_of_sale",
	"arca_voucher_number",
)


def applies(doc) -> bool:
	"""Purchase invoices of companies set up for ARCA, from suppliers that issue ARCA vouchers."""
	return (
		doc.doctype == "Purchase Invoice"
		and bool(doc.company)
		and bool(frappe.db.exists("ARCA Company Settings", doc.company))
		and not (doc.supplier and frappe.db.get_value("Supplier", doc.supplier, "arca_no_vouchers"))
	)


def verified_data(doc) -> str:
	return "|".join(_text(doc.get(field)) for field in VERIFIED_FIELDS)


def _text(value) -> str:
	if isinstance(value, float | int) and not isinstance(value, bool):
		return f"{flt(value, 2):.2f}"
	return str(value or "")


def is_verified(doc) -> bool:
	return doc.get("arca_verification") == VERIFIED and doc.get("arca_verified_data") == verified_data(doc)


# ---- Purchase Invoice -> what WSCDC receives ----


def missing_data(doc) -> list[str]:
	"""Labels of what is still needed to verify the voucher."""
	missing = []
	if not is_valid_cuit(normalize_cuit(doc.tax_id or "")):
		missing.append(_("the supplier's CUIT (Tax ID of the supplier)"))
	for field, label in (
		("bill_date", _("Supplier Invoice Date")),
		("arca_voucher_type", _("ARCA Voucher Type")),
		("arca_point_of_sale", _("Point of Sale")),
		("arca_voucher_number", _("Voucher Number")),
		("arca_authorization_code", _("Authorization Code")),
	):
		if not doc.get(field):
			missing.append(label)
	return missing


def read_voucher(doc, settings) -> ReceivedVoucher:
	return ReceivedVoucher(
		mode=doc.arca_authorization_mode or "CAE",
		issuer_cuit=normalize_cuit(doc.tax_id),
		point_of_sale=int(doc.arca_point_of_sale),
		voucher_type=int(doc.arca_voucher_type),
		number=int(doc.arca_voucher_number),
		issue_date=getdate(doc.bill_date),
		total=abs(flt(doc.grand_total, 2)),
		authorization_code=(doc.arca_authorization_code or "").strip(),
		receiver_document_type=CUIT_DOCUMENT,
		receiver_document_number=settings.cuit,
	)


# ---- Verify ----


@frappe.whitelist(methods=["POST"])
def verify_in_arca(name: str) -> dict:
	"""Ask ARCA whether the voucher of draft purchase invoice `name` is genuine."""
	doc = frappe.get_doc("Purchase Invoice", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only draft purchase invoices can be verified."))
	if not applies(doc):
		frappe.throw(_("This purchase invoice is not verified with ARCA."))
	verify(doc)
	return status(doc)


def verify(doc) -> Verification:
	"""Ask WSCDC and keep the answer on `doc`. Raises if ARCA cannot be asked."""
	if missing := missing_data(doc):
		frappe.throw(
			_("To verify the voucher with ARCA, complete: {0}.").format(", ".join(missing)),
			title=_("ARCA Verification"),
		)
	settings = frappe.get_cached_doc("ARCA Company Settings", doc.company)
	try:
		client = get_wscdc_client(doc.company)
	except ArcaError as exc:
		_throw_failure(exc, settings)
	try:
		result = client.verify(read_voucher(doc, settings))
	except ArcaError as exc:
		_log(doc, settings, client, "Failed", str(exc))
		_throw_failure(exc, settings)

	answer = "\n".join(f"{o.code}: {o.description}" for o in result.observations)  # ARCA's own words
	values = {
		"arca_verification": VERIFIED if result.approved else REJECTED,
		"arca_verified_on": now_datetime(),
		"arca_verification_message": answer,
		"arca_verified_data": verified_data(doc),
	}
	doc.update(values)
	if not doc.is_new():
		frappe.db.set_value("Purchase Invoice", doc.name, values, update_modified=False)
	_log(doc, settings, client, VERIFIED if result.approved else REJECTED, answer)
	return result


def explain(doc) -> str:
	"""ARCA's answer, kept as "code: ARCA's text" lines, in the user's language."""
	lines = []
	for line in (doc.get("arca_verification_message") or "").splitlines():
		code, _sep, text = line.partition(": ")
		lines.append(_(EXPLANATIONS[code]) if code in EXPLANATIONS else line)
	if lines:
		return "\n".join(lines)
	if doc.get("arca_verification") == VERIFIED:
		return _("ARCA confirms the voucher.")
	return _("ARCA did not confirm the voucher.")


def _throw_failure(exc: ArcaError, settings):
	what, todo = problem_message(
		classify(exc),
		settings.environment,
		settings.cuit,
		getattr(exc, "message", str(exc)),
		service=WSAA_SERVICE_IDS[Service.WSCDC],
	)
	frappe.throw(f"{what} {todo}".strip(), title=_("Could not verify the voucher with ARCA"))


def _log(doc, settings, client, result: str, message: str) -> None:
	values = {
		"doctype": LOG,
		"purchase_invoice": None if doc.is_new() else doc.name,
		"company": doc.company,
		"environment": settings.environment,
		"result": result,
		"voucher": f"{doc.arca_voucher_type}-{int(doc.arca_point_of_sale or 0):05d}-{int(doc.arca_voucher_number or 0):08d}",
		"cae": doc.arca_authorization_code,
		"message": message,
		"operation": ", ".join(e.operation for e in client.exchanges),
		"request": "\n\n".join(e.request for e in client.exchanges),
		"response": "\n\n".join(e.response for e in client.exchanges),
	}

	def insert():
		log = frappe.get_doc(values)
		log.flags.ignore_links = True  # a purchase invoice created in the same request may be rolled back
		log.insert(ignore_permissions=True)

	insert()
	# A rejected submit rolls back: write the log again after it, so the answer is kept.
	frappe.db.after_rollback.add(lambda: (insert(), None if frappe.flags.in_test else frappe.db.commit()))


# ---- The QR ----


@frappe.whitelist()
def read_qr(content: str, company: str | None = None, supplier: str | None = None) -> dict:
	"""The values the QR of a received voucher fills in, and what does not match."""
	frappe.has_permission("Purchase Invoice", "write", throw=True)
	try:
		voucher = qr.parse(content)
	except qr.InvalidQr:
		frappe.throw(_("This is not the QR of an ARCA voucher."), title=_("Read QR"))

	values = {
		"arca_authorization_mode": voucher.authorization_mode,
		"arca_authorization_code": voucher.authorization_code,
		"arca_voucher_type": str(voucher.voucher_type),
		"arca_point_of_sale": voucher.point_of_sale,
		"arca_voucher_number": voucher.number,
		"bill_no": f"{voucher.point_of_sale:05d}-{voucher.number:08d}",
		"bill_date": voucher.issue_date.isoformat(),
	}
	warnings = []
	found = _supplier_with_cuit(voucher.issuer_cuit)
	if not supplier and found:
		values["supplier"] = found
	elif (
		supplier
		and normalize_cuit(frappe.db.get_value("Supplier", supplier, "tax_id") or "") != voucher.issuer_cuit
	):
		warnings.append(
			_("The voucher was issued by CUIT {0}, not by supplier {1}.").format(
				_cuit(voucher.issuer_cuit), supplier
			)
		)
	if not supplier and not found:
		warnings.append(
			_("No supplier has CUIT {0}. Create it and set that CUIT in its Tax ID.").format(
				_cuit(voucher.issuer_cuit)
			)
		)
	if company:
		own = frappe.db.get_value("ARCA Company Settings", company, "cuit")
		if own and voucher.receiver_document_number and voucher.receiver_document_number != own:
			warnings.append(
				_("The voucher was issued to CUIT {0}, not to {1}.").format(
					_cuit(voucher.receiver_document_number), company
				)
			)
	if not frappe.db.exists("ARCA Voucher Type", values["arca_voucher_type"]):
		warnings.append(
			_("ARCA voucher type {0} is unknown: sync the ARCA catalogs.").format(voucher.voucher_type)
		)
		values.pop("arca_voucher_type")
	return {
		"values": values,
		"total": float(voucher.total),
		"currency": voucher.currency,
		"warnings": warnings,
	}


def _supplier_with_cuit(cuit: str) -> str | None:
	for tax_id in (cuit, _cuit(cuit)):
		if name := frappe.db.get_value("Supplier", {"tax_id": tax_id, "disabled": 0}, "name"):
			return name
	return None


def _cuit(cuit: str) -> str:
	return f"{cuit[:2]}-{cuit[2:10]}-{cuit[10:]}" if len(cuit) == 11 else cuit


# ---- What the form shows ----


@frappe.whitelist()
def get_status(name: str) -> dict:
	doc = frappe.get_doc("Purchase Invoice", name)
	doc.check_permission("read")
	return status(doc)


def status(doc) -> dict:
	if not applies(doc):
		return {"applies": False}
	return {
		"applies": True,
		"verified": is_verified(doc),
		"verification": doc.get("arca_verification") or "",
		"outdated": bool(doc.get("arca_verification"))
		and doc.get("arca_verified_data") != verified_data(doc),
		"verified_on": doc.get("arca_verified_on"),
		"message": explain(doc) if doc.get("arca_verification") else "",
		"missing": missing_data(doc),
	}


# ---- doc_events ----


def validate(doc, method=None):
	"""A change in what ARCA checked voids the verification."""
	if doc.get("arca_verification") and doc.get("arca_verified_data") != verified_data(doc):
		doc.arca_verification = ""
		doc.arca_verification_message = ""
		doc.arca_verified_on = None
		doc.arca_verified_data = ""


def before_submit(doc, method=None):
	"""Only vouchers ARCA confirms are accepted."""
	if not applies(doc) or is_verified(doc):
		return
	result = verify(doc)
	if not result.approved:
		frappe.throw(
			explain(doc),
			title=_("ARCA does not confirm this voucher"),
		)


def validate_supplier(doc, method=None):
	"""Only an Accounts Manager can exempt a supplier from the verification."""
	changed = doc.has_value_changed("arca_no_vouchers") if not doc.is_new() else bool(doc.arca_no_vouchers)
	if changed and EXEMPTING_ROLE not in frappe.get_roles():
		frappe.throw(
			_("Only an Accounts Manager can change 'Does Not Issue ARCA Vouchers'."),
			frappe.PermissionError,
		)
