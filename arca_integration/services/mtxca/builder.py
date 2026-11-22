"""Turn a draft invoice into the voucher ARCA expects, deducing what the user did not set.

Input and output are plain data: the Frappe adapter reads Sales Invoice into a
`Draft` and the catalogs into `Rules`. `review` returns the voucher together
with every issue that would make ARCA reject it, so the preview shows exactly
what will be sent.
"""

import calendar
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from .validation import (
	Check,
	Issue,
	check_exchange_rate,
	check_issue_date,
	check_receiver,
	check_totals,
)
from .voucher import (
	LETTER_OF,
	UNTAXED,
	VOUCHER_TYPES,
	AssociatedVoucher,
	Concept,
	Kind,
	Letter,
	OtherTax,
	VatSubtotal,
	Voucher,
	VoucherItem,
)

CENT = Decimal("0.01")
MICRO = Decimal("0.000001")
PESOS = "PES"


def q2(value: Decimal) -> Decimal:
	return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def q6(value: Decimal) -> Decimal:
	return value.quantize(MICRO, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class DraftLine:
	row: int
	description: str
	item_code: str
	quantity: Decimal
	net_amount: Decimal  # after discounts and without VAT, in the invoice currency
	unit: str  # ARCA unit code, "" if the UOM is not mapped
	vat_condition: str  # ARCA VAT condition code, "" if unknown
	gtin: str
	is_stock_item: bool


@dataclass(frozen=True)
class DraftTax:
	row: int
	account: str
	description: str
	amount: Decimal
	base: Decimal
	is_vat: bool
	tax_type: str  # ARCA tax type code, "" if the account is not mapped


@dataclass(frozen=True)
class Receiver:
	name: str
	vat_condition: str  # ARCA receiver VAT condition code
	document_type: str = ""
	document_number: str = ""  # digits only


@dataclass(frozen=True)
class Draft:
	issue_date: date
	receiver: Receiver
	lines: tuple[DraftLine, ...]
	grand_total: Decimal
	currency: str  # ARCA currency code, "" if not mapped
	exchange_rate: Decimal = Decimal(1)
	taxes: tuple[DraftTax, ...] = ()
	point_of_sale: int | None = None
	concept: Concept | None = None  # None: deduce it from the items
	service_start: date | None = None
	service_end: date | None = None
	due_date: date | None = None
	kind: str = Kind.INVOICE
	# Credit and debit notes: the authorized invoice they correct, and its ERPNext name.
	associated: AssociatedVoucher | None = None
	associated_name: str = ""
	# The letter ARCA already gave this voucher: it is kept.
	letter: str | None = None
	# Foreign currency: whether it is paid in that same currency, and ARCA's official rate
	# for the issue date (None when unknown: ARCA still checks it when authorizing).
	paid_in_currency: bool = False
	official_rate: Decimal | None = None


@dataclass(frozen=True)
class Rules:
	issuer_cuit: str
	# ARCA VAT condition code -> rate in percent; None for untaxed and exempt.
	vat_rates: Mapping[str, Decimal | None]
	# Receiver VAT condition code -> voucher types it may receive (consultarCondicionesIVAReceptor).
	receiver_voucher_types: Mapping[str, tuple[int, ...]]
	# RG 4444: from this total in pesos, a B voucher must identify the receiver.
	anonymous_limit: Decimal
	today: date


@dataclass(frozen=True)
class Review:
	voucher: Voucher | None
	issues: tuple[Issue, ...] = field(default_factory=tuple)
	concept: Concept | None = None
	letter: str | None = None

	@property
	def ready(self) -> bool:
		return self.voucher is not None and not self.issues


def deduce_concept(lines: tuple[DraftLine, ...]) -> Concept:
	"""Stock items are products; anything else is a service."""
	products = {line.is_stock_item for line in lines}
	if products == {True}:
		return Concept.PRODUCTS
	if products == {False}:
		return Concept.SERVICES
	return Concept.PRODUCTS_AND_SERVICES


def default_service_period(issue_date: date) -> tuple[date, date]:
	"""The month of the invoice."""
	last_day = calendar.monthrange(issue_date.year, issue_date.month)[1]
	return issue_date.replace(day=1), issue_date.replace(day=last_day)


def deduce_letter(receiver_vat_condition: str, rules: Rules, kind: str = Kind.INVOICE) -> str | None:
	"""A when the receiver may get it, else B; None when it may get neither."""
	allowed = rules.receiver_voucher_types.get(receiver_vat_condition, ())
	for letter in (Letter.A, Letter.B):
		if VOUCHER_TYPES[letter][kind] in allowed:
			return letter
	return None


def build_item(line: DraftLine, letter: str, rate: Decimal) -> VoucherItem:
	"""Amounts for one item (514, 515, 519, 521)."""
	gross = q2(line.net_amount * (1 + rate))
	if letter == Letter.A:
		vat = q2(line.net_amount * rate)
		amount, unit_price = line.net_amount + vat, q6(line.net_amount / line.quantity)
	else:
		vat, amount, unit_price = None, gross, q6(gross / line.quantity)
	return VoucherItem(
		description=line.description,
		code=line.item_code,
		quantity=q6(line.quantity),
		unit=line.unit,
		unit_price=unit_price,
		vat_condition=line.vat_condition,
		vat_amount=vat,
		amount=q2(amount),
		gtin=line.gtin,
		gtin_units=1 if line.gtin else None,
	)


def item_vat(item: VoucherItem, rate: Decimal) -> Decimal:
	"""VAT included in an item; B items carry it inside the amount."""
	if item.vat_amount is not None:
		return item.vat_amount
	return q2(item.amount - item.amount / (1 + rate))


def _check_associated(draft: Draft) -> list[Issue]:
	if draft.associated is None:
		if draft.associated_name:
			return [Issue(Check.ASSOCIATED_NOT_AUTHORIZED, values=(draft.associated_name,))]
		return [Issue(Check.ASSOCIATED_MISSING)]
	if draft.associated.issue_date and draft.associated.issue_date > draft.issue_date:
		return [Issue(Check.ASSOCIATED_AFTER_NOTE, values=(draft.associated.issue_date,))]
	return []


def review(draft: Draft, rules: Rules) -> Review:
	issues: list[Issue] = []
	concept = draft.concept or deduce_concept(draft.lines)
	letter = deduce_letter(draft.receiver.vat_condition, rules, draft.kind)
	if draft.kind != Kind.INVOICE:
		issues += _check_associated(draft)
		if draft.associated:
			letter = LETTER_OF[draft.associated.voucher_type]  # a note has its invoice's letter (200)
	if draft.letter:
		letter = draft.letter

	if draft.point_of_sale is None:
		issues.append(Issue(Check.NO_POINT_OF_SALE))
	if not draft.currency:
		issues.append(Issue(Check.CURRENCY_MISSING))
	if issue := check_issue_date(draft.issue_date, concept, rules.today):
		issues.append(issue)

	service_start = service_end = payment_due = None
	if concept != Concept.PRODUCTS:
		default_start, default_end = default_service_period(draft.issue_date)
		service_start = draft.service_start or default_start
		service_end = draft.service_end or default_end
		payment_due = draft.due_date or draft.issue_date
		if service_start > service_end:
			issues.append(Issue(Check.SERVICE_DATES_ORDER))
		if payment_due < draft.issue_date:
			issues.append(Issue(Check.PAYMENT_DUE_BEFORE_ISSUE))

	known_conditions = True
	for line in draft.lines:
		if line.vat_condition not in rules.vat_rates:
			issues.append(Issue(Check.LINE_VAT_CONDITION_MISSING, line.row, (line.item_code,)))
			known_conditions = False
		if not line.unit:
			issues.append(Issue(Check.LINE_UNIT_MISSING, line.row, (line.item_code,)))
		if not line.gtin:
			issues.append(Issue(Check.LINE_GTIN_MISSING, line.row, (line.item_code,)))
		if line.quantity <= 0 or line.net_amount < 0:
			issues.append(Issue(Check.LINE_NOT_POSITIVE, line.row, (line.item_code,)))

	other_taxes, vat_in_invoice = [], Decimal(0)
	for tax in draft.taxes:
		if tax.is_vat:
			vat_in_invoice += tax.amount
		elif tax.tax_type:
			other_taxes.append(OtherTax(tax.tax_type, tax.description, q2(tax.base), q2(tax.amount)))
		else:
			issues.append(Issue(Check.TAX_ACCOUNT_UNMAPPED, tax.row, (tax.account,)))

	foreign = bool(draft.currency) and draft.currency != PESOS
	exchange_rate = draft.exchange_rate if foreign else Decimal(1)  # 120
	# The mark is only for invoices (118) in a foreign currency (169).
	paid_in_currency = draft.paid_in_currency if foreign and draft.kind == Kind.INVOICE else None
	if foreign:
		issues += check_exchange_rate(
			draft.currency, exchange_rate, draft.official_rate, bool(paid_in_currency)
		)
	total_in_pesos = draft.grand_total * exchange_rate
	issues += check_receiver(
		letter,
		draft.receiver.vat_condition,
		draft.receiver.document_type,
		draft.receiver.document_number,
		rules.issuer_cuit,
		total_in_pesos,
		rules.anonymous_limit,
	)

	if letter is None or not known_conditions or any(i.check == Check.LINE_NOT_POSITIVE for i in issues):
		return Review(None, tuple(issues), concept, letter)

	items, taxed, untaxed, exempt = [], Decimal(0), Decimal(0), Decimal(0)
	vat_by_condition: dict[str, Decimal] = {}
	for line in draft.lines:
		rate = (rules.vat_rates[line.vat_condition] or Decimal(0)) / 100
		item = build_item(line, letter, rate)
		items.append(item)
		if rules.vat_rates[line.vat_condition] is None:
			if line.vat_condition == UNTAXED:
				untaxed += item.amount
			else:
				exempt += item.amount
			continue
		vat = item_vat(item, rate)
		taxed += item.amount - vat
		if rate:
			vat_by_condition[line.vat_condition] = vat_by_condition.get(line.vat_condition, Decimal(0)) + vat

	vat_subtotals = tuple(VatSubtotal(code, amount) for code, amount in sorted(vat_by_condition.items()))
	other_taxes_total = sum((tax.amount for tax in other_taxes), Decimal(0))
	subtotal = untaxed + taxed + exempt
	voucher = Voucher(
		voucher_type=VOUCHER_TYPES[letter][draft.kind],
		point_of_sale=draft.point_of_sale or 0,
		issue_date=draft.issue_date,
		receiver_vat_condition=draft.receiver.vat_condition,
		document_type=draft.receiver.document_type,
		document_number=draft.receiver.document_number,
		concept=concept,
		service_start=service_start,
		service_end=service_end,
		payment_due=payment_due,
		associated=(draft.associated,) if draft.associated else (),
		currency=draft.currency,
		exchange_rate=exchange_rate,
		paid_in_currency=paid_in_currency,
		items=tuple(items),
		vat_subtotals=vat_subtotals,
		other_taxes=tuple(other_taxes),
		taxed=taxed,
		untaxed=untaxed,
		exempt=exempt,
		subtotal=subtotal,
		other_taxes_total=other_taxes_total,
		total=subtotal + other_taxes_total + sum(vat_by_condition.values(), Decimal(0)),
	)
	issues += check_totals(voucher, vat_in_invoice, draft.grand_total)
	return Review(voucher, tuple(issues), concept, letter)
