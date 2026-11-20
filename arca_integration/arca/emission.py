"""Request the CAE when a Sales Invoice is submitted.

The flow (manual v0.25.10, p. 13 and 45):
1. `before_submit`: the review must pass, and the WSAA ticket is obtained
   before the invoice is written, because a login commits.
2. `on_submit`, under a lock per point of sale and voucher type:
   - compare ARCA's last number with ours; if ARCA has one more, an earlier
     answer was lost: adopt that voucher instead of issuing another;
   - authorize number last + 1;
   - on a timeout, ask ARCA whether the voucher exists before giving up.
   A rejection or a failure raises, so the invoice stays in draft.
Every exchange is kept in ARCA Request Log, also when the submit rolls back.
"""

from dataclasses import replace

import frappe
from frappe import _
from frappe.utils.synchronization import filelock

from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import get_access_ticket
from arca_integration.arca.invoice import CONCEPT_LABELS, applies, review
from arca_integration.arca.mtxca import get_mtxca_client
from arca_integration.services.endpoints import Service
from arca_integration.services.errors import ArcaError, ServiceFault, TransportTimeout
from arca_integration.services.mtxca.client import AuthorizedVoucher, MtxcaClient, MtxcaError
from arca_integration.services.mtxca.messages import explain
from arca_integration.services.mtxca.voucher import Voucher

LOG = "ARCA Request Log"
AUTHORIZED, OBSERVED, REJECTED, ADOPTED, FAILED = "Authorized", "Observed", "Rejected", "Adopted", "Failed"


def prepare(doc, method=None) -> None:
	"""Log in to WSAA now: a login commits, and on_submit runs after the invoice is written."""
	if applies(doc):
		get_access_ticket(doc.company, Service.WSMTXCA)


def emit(doc, method=None) -> None:
	if not applies(doc):
		return
	result = review(doc)
	if not result.ready:  # before_submit already checked; the invoice changed in between
		frappe.throw(_("The invoice changed while it was being submitted. Review it and try again."))

	settings = frappe.get_cached_doc("ARCA Company Settings", doc.company)
	voucher = result.voucher
	client = get_mtxca_client(doc.company)
	lock = f"arca-emit-{frappe.scrub(doc.company)}-{settings.environment}-{voucher.point_of_sale}-{voucher.voucher_type}"

	with filelock(lock, timeout=60):
		try:
			last = client.last_authorized(voucher.voucher_type, voucher.point_of_sale)
			known = _last_known(doc, settings, voucher)
			if last > known and _recover(client, doc, settings, voucher, last, first_use=not known):
				return
			_authorize(client, doc, settings, replace(voucher, number=last + 1))
		except ArcaError as exc:
			_fail(client, doc, settings, exc)


def _last_known(doc, settings, voucher: Voucher) -> int:
	"""Highest number this site recorded for the point of sale and voucher type."""
	numbers = frappe.get_all(
		"Sales Invoice",
		filters={
			"company": doc.company,
			"arca_point_of_sale": settings.default_point_of_sale,
			"arca_voucher_type": str(voucher.voucher_type),
			"docstatus": ["in", [1, 2]],
			"name": ["!=", doc.name],
		},
		pluck="arca_voucher_number",
		order_by="arca_voucher_number desc",
		limit=1,
	)
	return int(numbers[0] or 0) if numbers else 0


def _recover(client: MtxcaClient, doc, settings, voucher: Voucher, number: int, first_use: bool) -> bool:
	"""ARCA has a voucher this site never recorded. Adopt it if it is this invoice (True).

	Otherwise, the first time this site uses the point of sale and voucher type,
	the earlier vouchers came from another system or an earlier installation:
	numbering continues after them (False). Later on, a foreign voucher means
	another system is using the point of sale, so the submit stops.
	"""
	found = client.get_voucher(voucher.voucher_type, voucher.point_of_sale, number)
	if found and _same_voucher(found, voucher):
		_record(doc, settings, voucher, found.number, found.cae, found.cae_due_date)
		_log(doc, settings, client, ADOPTED, _voucher_id(voucher, found.number), cae=found.cae)
		frappe.msgprint(
			_("ARCA had already authorized this invoice (CAE {0}). The CAE was recovered.").format(found.cae),
			indicator="green",
			alert=True,
		)
		return True
	if first_use:
		return False

	_log(doc, settings, client, FAILED, _voucher_id(voucher, number), message="numbering out of sync")
	frappe.throw(
		_(
			"ARCA has voucher {0} for this point of sale, but this site did not issue it. Another system may be using the point of sale. Use a point of sale only for ERPNext."
		).format(_voucher_id(voucher, number))
	)


def _same_voucher(found: AuthorizedVoucher, voucher: Voucher) -> bool:
	return (
		abs(found.total - voucher.total) < 1
		and found.document_number.lstrip("0") == (voucher.document_number or "").lstrip("0")
		and (found.issue_date is None or found.issue_date == voucher.issue_date)
	)


def _authorize(client: MtxcaClient, doc, settings, voucher: Voucher) -> None:
	voucher_id = _voucher_id(voucher, voucher.number)
	try:
		authorization = client.authorize(voucher)
	except TransportTimeout:
		# ARCA may have authorized it: ask before telling the user anything.
		found = client.get_voucher(voucher.voucher_type, voucher.point_of_sale, voucher.number)
		if found and _same_voucher(found, voucher):
			_record(doc, settings, voucher, found.number, found.cae, found.cae_due_date)
			_log(doc, settings, client, ADOPTED, voucher_id, cae=found.cae)
			return
		raise
	except MtxcaError as exc:
		_log(doc, settings, client, REJECTED, voucher_id, message=str(exc))
		frappe.throw(_rejection_message(exc), title=_("ARCA rejected the invoice"))

	_record(doc, settings, voucher, authorization.number, authorization.cae, authorization.cae_due_date)
	observations = [_explain_code(o.code, o.description) for o in authorization.observations]
	_log(
		doc,
		settings,
		client,
		OBSERVED if authorization.result == "O" else AUTHORIZED,
		voucher_id,
		cae=authorization.cae,
		message="\n".join(f"{o.code}: {o.description}" for o in authorization.observations),
	)
	if observations:
		frappe.msgprint(
			"<br>".join(observations),
			title=_("Authorized with observations"),
			indicator="orange",
		)


def _record(doc, settings, voucher: Voucher, number: int, cae: str, cae_due_date) -> None:
	doc.db_set(
		{
			"arca_status": "Authorized",
			"arca_voucher_type": str(voucher.voucher_type),
			"arca_point_of_sale": settings.default_point_of_sale,
			"arca_voucher_number": number,
			"arca_cae": cae,
			"arca_cae_due_date": cae_due_date,
			"arca_concept": doc.arca_concept or CONCEPT_LABELS[voucher.concept],
			"arca_service_start_date": voucher.service_start,
			"arca_service_end_date": voucher.service_end,
		}
	)


def _fail(client: MtxcaClient, doc, settings, exc: ArcaError) -> None:
	_log(
		doc,
		settings,
		client,
		FAILED,
		"",
		message=f"{type(exc).__name__}: {exc.message}\n{getattr(exc, 'detail', '') or ''}".strip(),
	)
	if isinstance(exc, MtxcaError):
		frappe.throw(_rejection_message(exc), title=_("ARCA"))
	if isinstance(exc, ServiceFault):
		message = _("ARCA answered with a technical error, so the invoice was not submitted: {0}").format(
			frappe.utils.escape_html(exc.message)
		)
	elif isinstance(exc, TransportTimeout):
		message = _(
			"ARCA did not answer in time and it could not be confirmed whether it authorized the invoice. The invoice was not submitted. When you try again, this site first asks ARCA and recovers the CAE if it was issued."
		)
	else:
		message = _("ARCA is not responding. Your invoice was not submitted; try again in a few minutes.")
	detail = frappe.utils.escape_html(
		f"{type(exc).__name__}: {exc.message} {getattr(exc, 'detail', '') or ''}"
	)
	frappe.throw(
		f"{message}<details class='mt-2 small text-muted'><summary>{_('Technical detail')}</summary>{detail}</details>",
		title=_("ARCA"),
	)


def _rejection_message(exc: MtxcaError) -> str:
	return "<br>".join(_explain_code(e.code, e.description) for e in exc.errors)


def _explain_code(code: str, description: str) -> str:
	esc = frappe.utils.escape_html
	technical = f"<span class='text-muted small'>({esc(code)}: {esc(description)})</span>"
	if explanation := explain(code):
		return f"{esc(_(explanation.message))} {esc(_(explanation.action))} {technical}"
	return f"{esc(description)} <span class='text-muted small'>({esc(code)})</span>"


def _voucher_id(voucher: Voucher, number: int | None) -> str:
	return f"{voucher.voucher_type:03d}-{voucher.point_of_sale:05d}-{(number or 0):08d}"


# ---- Request log ----


def _log(doc, settings, client: MtxcaClient, result: str, voucher_id: str, cae: str = "", message: str = ""):
	"""Keep the exchanges; failed submits roll back, so their log is written after the rollback."""
	values = {
		"doctype": LOG,
		"sales_invoice": doc.name,
		"company": doc.company,
		"environment": settings.environment,
		"result": result,
		"voucher": voucher_id,
		"cae": cae,
		"message": message,
		"operation": ", ".join(exchange.operation for exchange in client.exchanges),
		"request": "\n\n".join(exchange.request for exchange in client.exchanges),
		"response": "\n\n".join(exchange.response for exchange in client.exchanges),
	}
	if result in (AUTHORIZED, OBSERVED, ADOPTED):
		frappe.get_doc(values).insert(ignore_permissions=True)
	else:
		frappe.db.after_rollback.add(lambda: _insert_and_commit(values))


def _insert_and_commit(values: dict) -> None:
	log = frappe.get_doc(values)
	log.flags.ignore_links = True  # an invoice created in the same request was rolled back too
	log.insert(ignore_permissions=True)
	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep: the submit rolled back; the log must survive it


# ---- doc_events ----


def before_cancel(doc, method=None):
	if doc.get("arca_cae"):
		frappe.throw(
			_(
				"An invoice authorized by ARCA cannot be cancelled or deleted. To correct it, issue a credit note."
			),
			title=_("Authorized by ARCA"),
		)
