"""ARCA's official exchange rates, for invoices in a foreign currency (RG 5616)."""

from datetime import date
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import getdate

from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import obtain_ticket
from arca_integration.arca.mtxca import mtxca_client_for
from arca_integration.services.endpoints import Service
from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.builder import PESOS
from arca_integration.services.mtxca.validation import rate_date

CACHE_SECONDS = 3600
CACHE_SECONDS_MISSING = 600  # the rate of the day may still be published


def official_rate(company: str, currency: str, issue_date: date) -> Decimal | None:
	"""The rate ARCA would apply to a voucher of `issue_date` in ARCA currency `currency`.

	None for pesos, or when ARCA cannot be asked or has no rate: ARCA still checks
	the rate when it authorizes the voucher.
	"""
	if not currency or currency == PESOS:
		return None
	settings = frappe.get_cached_doc("ARCA Company Settings", company)
	day = rate_date(getdate(issue_date), getdate())
	key = f"arca_rate:{settings.environment}:{currency}:{day.isoformat()}"
	cached = frappe.cache.get_value(key)
	if cached is not None:
		return Decimal(cached) if cached else None

	try:
		# obtain_ticket raises instead of showing a message: a failure here is not the user's problem.
		client = mtxca_client_for(company, obtain_ticket(company, Service.WSMTXCA))
		rate = client.exchange_rate(currency, day)
	except (ArcaError, frappe.ValidationError):
		frappe.clear_last_message()
		return None
	frappe.cache.set_value(
		key, str(rate) if rate else "", expires_in_sec=CACHE_SECONDS if rate else CACHE_SECONDS_MISSING
	)
	return rate


@frappe.whitelist()
def get_official_rate(company: str, currency: str, posting_date: str) -> dict:
	"""ARCA's rate for an invoice of `company` in ERPNext `currency` dated `posting_date`."""
	frappe.has_permission("Sales Invoice", "write", throw=True)
	code = frappe.db.get_value("Currency", currency, "arca_currency")
	if not code:
		frappe.throw(_("Currency {0} has no ARCA currency.").format(currency))
	rate = official_rate(company, code, posting_date)
	day = rate_date(getdate(posting_date), getdate())
	if rate is None:
		frappe.throw(
			_("ARCA has no exchange rate for {0} on {1}.").format(currency, frappe.format(day, "Date"))
		)
	return {"rate": float(rate), "date": day}
