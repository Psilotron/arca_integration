from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from frappe.tests import UnitTestCase

from arca_integration.services.mtxca.builder import (
	Draft,
	DraftLine,
	DraftTax,
	Receiver,
	Rules,
	deduce_concept,
	default_service_period,
	review,
)
from arca_integration.services.mtxca.validation import Check, tolerance
from arca_integration.services.mtxca.voucher import Concept, OtherTax, VatSubtotal

TODAY = date(2010, 11, 1)
RULES = Rules(
	issuer_cuit="30000000015",
	vat_rates={"1": None, "2": None, "3": D(0), "4": D("10.5"), "5": D(21), "6": D(27)},
	receiver_voucher_types={"1": (1, 2, 3), "6": (1, 2, 3), "5": (6, 7, 8), "4": (6, 7, 8)},
	anonymous_limit=D(10_000_000),
	today=TODAY,
)
RI = Receiver("ACME SA", "1", "80", "30000000007")
FINAL_CONSUMER = Receiver("Juan", "5", "96", "24999999")


def line(row=1, net="100", condition="5", stock=True, **kwargs):
	values = {
		"row": row,
		"description": f"Producto {row}",
		"item_code": f"P{row:04d}",
		"quantity": D(1),
		"net_amount": D(net),
		"unit": "7",
		"vat_condition": condition,
		"gtin": "0123456789913",
		"is_stock_item": stock,
	}
	return DraftLine(**{**values, **kwargs})


def other_tax(amount, base="100", row=2):
	return DraftTax(row, "Percepciones", "Otro Tributo", D(amount), D(base), False, "99")


def vat(amount, row=1):
	return DraftTax(row, "IVA Débito Fiscal", "IVA 21%", D(amount), D(100), True, "")


def draft(**kwargs):
	values = {
		"issue_date": TODAY,
		"receiver": RI,
		"lines": (line(),),
		"taxes": (vat("21"), other_tax("1.00")),
		"grand_total": D("122.00"),
		"currency": "PES",
		"point_of_sale": 4000,
	}
	return Draft(**{**values, **kwargs})


def checks(result):
	return {issue.check for issue in result.issues}


class TestBuilder(UnitTestCase):
	def test_invoice_a_from_the_manual(self):
		"""Manual v0.25.10, p. 30: Factura A to a VAT registered receiver."""
		result = review(draft(), RULES)

		self.assertTrue(result.ready, result.issues)
		voucher = result.voucher
		self.assertEqual((voucher.voucher_type, voucher.letter, voucher.concept), (1, "A", Concept.PRODUCTS))
		self.assertEqual(
			(voucher.taxed, voucher.untaxed, voucher.exempt, voucher.subtotal),
			(D("100.00"), 0, 0, D("100.00")),
		)
		self.assertEqual((voucher.other_taxes_total, voucher.total), (D("1.00"), D("122.00")))
		self.assertEqual(voucher.vat_subtotals, (VatSubtotal("5", D("21.00")),))
		self.assertEqual(voucher.other_taxes, (OtherTax("99", "Otro Tributo", D("100.00"), D("1.00")),))
		item = voucher.items[0]
		self.assertEqual(
			(item.unit_price, item.vat_amount, item.amount, item.gtin_units),
			(D("100.000000"), D("21.00"), D("121.00"), 1),
		)

	def test_invoice_b_from_the_manual(self):
		"""Manual v0.25.10, p. 33: Factura B with an exempt item and another tax."""
		result = review(
			draft(
				receiver=FINAL_CONSUMER,
				lines=(line(1, "100", "5"), line(2, "100", "2")),
				taxes=(vat("21"), other_tax("0.01")),
				grand_total=D("221.01"),
			),
			RULES,
		)

		self.assertTrue(result.ready, result.issues)
		voucher = result.voucher
		self.assertEqual(voucher.voucher_type, 6)
		self.assertEqual(
			(voucher.taxed, voucher.exempt, voucher.subtotal, voucher.total),
			(D("100.00"), D("100.00"), D("200.00"), D("221.01")),
		)
		self.assertEqual(voucher.vat_subtotals, (VatSubtotal("5", D("21.00")),))
		first, second = voucher.items
		self.assertEqual(
			(first.unit_price, first.vat_amount, first.amount), (D("121.000000"), None, D("121.00"))
		)
		self.assertEqual(second.amount, D("100.00"))

	def test_mixed_rates_and_untaxed(self):
		result = review(
			draft(
				lines=(
					line(1, "100000", "5"),
					line(2, "50000", "4"),
					line(3, "30000", "2"),
					line(4, "10000", "1"),
				),
				taxes=(vat("26250"),),
				grand_total=D("216250"),
			),
			RULES,
		)
		self.assertTrue(result.ready, result.issues)
		voucher = result.voucher
		self.assertEqual(
			voucher.vat_subtotals, (VatSubtotal("4", D("5250.00")), VatSubtotal("5", D("21000.00")))
		)
		self.assertEqual(
			(voucher.taxed, voucher.exempt, voucher.untaxed, voucher.total),
			(D("150000.00"), D("30000.00"), D("10000.00"), D("216250.00")),
		)

	def test_zero_rate_is_taxed_without_subtotal(self):
		result = review(draft(lines=(line(condition="3"),), taxes=(), grand_total=D(100)), RULES)
		self.assertTrue(result.ready, result.issues)
		self.assertEqual((result.voucher.taxed, result.voucher.vat_subtotals), (D(100), ()))

	def test_monotributo_receives_a(self):
		receiver = Receiver("Mono", "6", "80", "20111111112")
		self.assertEqual(review(draft(receiver=receiver), RULES).letter, "A")

	def test_unit_price_keeps_six_decimals(self):
		result = review(
			draft(lines=(line(net="100", quantity=D(3)),), taxes=(vat("21"),), grand_total=D(121)), RULES
		)
		item = result.voucher.items[0]
		self.assertEqual(item.unit_price, D("33.333333"))
		self.assertLessEqual(abs(item.unit_price * item.quantity - D(100)), tolerance(D(100), 1))


class TestConceptAndDates(UnitTestCase):
	def test_concept_from_items(self):
		self.assertEqual(deduce_concept((line(stock=True),)), Concept.PRODUCTS)
		self.assertEqual(deduce_concept((line(stock=False),)), Concept.SERVICES)
		self.assertEqual(
			deduce_concept((line(stock=True), line(2, stock=False))), Concept.PRODUCTS_AND_SERVICES
		)

	def test_services_default_to_the_invoice_month(self):
		self.assertEqual(default_service_period(date(2024, 2, 10)), (date(2024, 2, 1), date(2024, 2, 29)))
		voucher = review(draft(lines=(line(stock=False),), due_date=date(2010, 11, 20)), RULES).voucher
		self.assertEqual(
			(voucher.service_start, voucher.service_end, voucher.payment_due),
			(date(2010, 11, 1), date(2010, 11, 30), date(2010, 11, 20)),
		)

	def test_products_carry_no_service_dates(self):
		voucher = review(draft(), RULES).voucher
		self.assertEqual(
			(voucher.service_start, voucher.service_end, voucher.payment_due), (None, None, None)
		)

	def test_issue_date_window(self):
		self.assertNotIn(
			Check.ISSUE_DATE_OUT_OF_RANGE, checks(review(draft(issue_date=date(2010, 10, 27)), RULES))
		)
		self.assertIn(
			Check.ISSUE_DATE_OUT_OF_RANGE, checks(review(draft(issue_date=date(2010, 10, 26)), RULES))
		)
		services = {"lines": (line(stock=False),)}
		self.assertNotIn(
			Check.ISSUE_DATE_OUT_OF_RANGE,
			checks(review(draft(issue_date=date(2010, 10, 22), **services), RULES)),
		)

	def test_products_cannot_be_dated_next_month(self):
		rules = replace(RULES, today=date(2010, 11, 29))
		self.assertIn(
			Check.ISSUE_DATE_OUT_OF_RANGE, checks(review(draft(issue_date=date(2010, 12, 1)), rules))
		)

	def test_service_dates_order_and_payment_due(self):
		result = review(
			draft(
				lines=(line(stock=False),),
				service_start=date(2010, 11, 30),
				service_end=date(2010, 11, 1),
				due_date=date(2010, 10, 30),
			),
			RULES,
		)
		self.assertTrue({Check.SERVICE_DATES_ORDER, Check.PAYMENT_DUE_BEFORE_ISSUE} <= checks(result))


class TestIssues(UnitTestCase):
	def test_receiver_without_vat_condition(self):
		result = review(draft(receiver=replace(RI, vat_condition="")), RULES)
		self.assertIsNone(result.voucher)
		self.assertIn(Check.RECEIVER_VAT_CONDITION_MISSING, checks(result))

	def test_receiver_condition_without_voucher_type(self):
		result = review(draft(receiver=replace(RI, vat_condition="99")), RULES)
		self.assertIn(Check.RECEIVER_VAT_CONDITION_NOT_ALLOWED, checks(result))

	def test_a_needs_cuit(self):
		result = review(draft(receiver=replace(RI, document_type="96", document_number="24999999")), RULES)
		self.assertIn(Check.RECEIVER_CUIT_REQUIRED, checks(result))

	def test_receiver_is_issuer(self):
		result = review(draft(receiver=replace(RI, document_number=RULES.issuer_cuit)), RULES)
		self.assertIn(Check.RECEIVER_IS_ISSUER, checks(result))

	def test_anonymous_consumer_up_to_the_limit(self):
		anonymous = Receiver("Consumidor Final", "5")
		small = {"lines": (line(net="1000", condition="3"),), "taxes": (), "grand_total": D(1000)}
		self.assertTrue(review(draft(receiver=anonymous, **small), RULES).ready)
		big = {"lines": (line(net="10000000", condition="3"),), "taxes": (), "grand_total": D(10_000_000)}
		self.assertIn(
			Check.RECEIVER_DOCUMENT_REQUIRED, checks(review(draft(receiver=anonymous, **big), RULES))
		)

	def test_document_type_without_number(self):
		result = review(draft(receiver=replace(FINAL_CONSUMER, document_number="")), RULES)
		self.assertIn(Check.RECEIVER_DOCUMENT_INCOMPLETE, checks(result))

	def test_line_issues_point_to_the_row(self):
		result = review(draft(lines=(line(), line(2, vat_condition="", unit="", gtin=""))), RULES)
		self.assertIsNone(result.voucher)
		self.assertEqual(
			{(issue.check, issue.row) for issue in result.issues},
			{
				(Check.LINE_VAT_CONDITION_MISSING, 2),
				(Check.LINE_UNIT_MISSING, 2),
				(Check.LINE_GTIN_MISSING, 2),
			},
		)

	def test_negative_line(self):
		result = review(draft(lines=(line(net="-10"),)), RULES)
		self.assertIn(Check.LINE_NOT_POSITIVE, checks(result))
		self.assertIsNone(result.voucher)

	def test_unmapped_tax_account(self):
		unmapped = DraftTax(3, "Fletes", "Fletes", D(5), D(100), False, "")
		result = review(draft(taxes=(vat("21"), other_tax("1.00"), unmapped)), RULES)
		self.assertEqual([(i.check, i.row) for i in result.issues], [(Check.TAX_ACCOUNT_UNMAPPED, 3)])

	def test_vat_and_total_must_match_the_invoice(self):
		result = review(draft(taxes=(vat("10.50"), other_tax("1.00")), grand_total=D("111.50")), RULES)
		self.assertEqual(checks(result), {Check.VAT_MISMATCH, Check.TOTAL_MISMATCH})

	def test_cents_within_tolerance(self):
		result = review(draft(taxes=(vat("21.01"), other_tax("1.00")), grand_total=D("122.01")), RULES)
		self.assertTrue(result.ready, result.issues)

	def test_missing_point_of_sale_and_currency(self):
		result = review(draft(point_of_sale=None, currency=""), RULES)
		self.assertTrue({Check.NO_POINT_OF_SALE, Check.CURRENCY_MISSING} <= checks(result))

	def test_foreign_currency_limit_uses_pesos(self):
		anonymous = Receiver("Consumidor Final", "5")
		result = review(
			draft(
				receiver=anonymous,
				currency="DOL",
				exchange_rate=D(1000),
				lines=(line(net="10000", condition="3"),),
				taxes=(),
				grand_total=D(10000),
			),
			RULES,
		)
		self.assertIn(Check.RECEIVER_DOCUMENT_REQUIRED, checks(result))
		self.assertEqual(result.voucher.exchange_rate, D(1000))
