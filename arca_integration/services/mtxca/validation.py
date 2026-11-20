"""Checks that a draft invoice passes ARCA's rules before anything is sent.

Each check prevents a rejection documented in the WSMTXCA manual (v0.25.10);
the error code is noted next to it. The result is a list of `Issue`: what is
wrong and where. The Frappe adapter turns each one into a message and an action.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .voucher import CUIT, Concept, Letter, Voucher


class Check(StrEnum):
	NO_POINT_OF_SALE = "no_point_of_sale"
	RECEIVER_VAT_CONDITION_MISSING = "receiver_vat_condition_missing"  # 190
	RECEIVER_VAT_CONDITION_NOT_ALLOWED = "receiver_vat_condition_not_allowed"  # 198
	RECEIVER_CUIT_REQUIRED = "receiver_cuit_required"  # 129
	RECEIVER_DOCUMENT_INCOMPLETE = "receiver_document_incomplete"  # 108
	RECEIVER_DOCUMENT_REQUIRED = "receiver_document_required"  # 128 (RG 4444)
	RECEIVER_IS_ISSUER = "receiver_is_issuer"  # 131
	CURRENCY_MISSING = "currency_missing"
	LINE_VAT_CONDITION_MISSING = "line_vat_condition_missing"  # 512
	LINE_UNIT_MISSING = "line_unit_missing"  # 508
	LINE_GTIN_MISSING = "line_gtin_missing"  # 503
	LINE_NOT_POSITIVE = "line_not_positive"  # 518
	TAX_ACCOUNT_UNMAPPED = "tax_account_unmapped"
	ISSUE_DATE_OUT_OF_RANGE = "issue_date_out_of_range"  # 103
	SERVICE_DATES_ORDER = "service_dates_order"  # 133
	PAYMENT_DUE_BEFORE_ISSUE = "payment_due_before_issue"  # 125
	VAT_MISMATCH = "vat_mismatch"
	TOTAL_MISMATCH = "total_mismatch"


@dataclass(frozen=True)
class Issue:
	check: Check
	row: int | None = None  # idx of the item or tax row, when the issue is about one
	values: tuple = ()  # what the message shows, e.g. the item code or the amounts


# Days before or after today that fechaEmision may be (103).
ISSUE_DATE_DAYS = {Concept.PRODUCTS: 5, Concept.SERVICES: 10, Concept.PRODUCTS_AND_SERVICES: 10}


def check_issue_date(issue_date: date, concept: Concept, today: date) -> Issue | None:
	days = ISSUE_DATE_DAYS[concept]
	offset = (issue_date - today).days
	next_month = concept == Concept.PRODUCTS and (issue_date.year, issue_date.month) > (
		today.year,
		today.month,
	)
	if abs(offset) > days or next_month:
		return Issue(Check.ISSUE_DATE_OUT_OF_RANGE, values=(days,))
	return None


def check_receiver(
	letter: str | None,
	receiver_vat_condition: str,
	document_type: str,
	document_number: str,
	issuer_cuit: str,
	total_in_pesos: Decimal,
	anonymous_limit: Decimal,
) -> list[Issue]:
	if not receiver_vat_condition:
		return [Issue(Check.RECEIVER_VAT_CONDITION_MISSING)]
	if letter is None:
		return [Issue(Check.RECEIVER_VAT_CONDITION_NOT_ALLOWED, values=(receiver_vat_condition,))]

	issues = []
	if bool(document_type) != bool(document_number):
		issues.append(Issue(Check.RECEIVER_DOCUMENT_INCOMPLETE))
	elif letter == Letter.A and document_type != CUIT:
		issues.append(Issue(Check.RECEIVER_CUIT_REQUIRED))
	elif not document_number and total_in_pesos >= anonymous_limit:
		issues.append(Issue(Check.RECEIVER_DOCUMENT_REQUIRED, values=(anonymous_limit,)))
	if document_number and document_number == issuer_cuit:
		issues.append(Issue(Check.RECEIVER_IS_ISSUER))
	return issues


def tolerance(expected: Decimal, lines: int) -> Decimal:
	"""ARCA's margin for computed amounts: 0.01 % relative or 0.01 per line (110-116, 519)."""
	return max(abs(expected) * Decimal("0.0001"), Decimal("0.01") * max(lines, 1))


def check_totals(voucher: Voucher, vat_in_invoice: Decimal, grand_total: Decimal) -> list[Issue]:
	"""Compare what ARCA will receive with what the invoice says."""
	issues = []
	lines = len(voucher.items)
	if abs(voucher.vat_total - vat_in_invoice) > tolerance(vat_in_invoice, lines):
		issues.append(Issue(Check.VAT_MISMATCH, values=(voucher.vat_total, vat_in_invoice)))
	if abs(voucher.total - grand_total) > tolerance(grand_total, lines):
		issues.append(Issue(Check.TOTAL_MISMATCH, values=(voucher.total, grand_total)))
	return issues
