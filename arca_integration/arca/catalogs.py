"""Keep the ARCA catalog DocTypes in sync with WSMTXCA.

Codes that ARCA stops returning are disabled, never deleted: invoices and
master data may still link to them.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import frappe
from frappe import _

from arca_integration.arca.doctype.arca_point_of_sale.arca_point_of_sale import point_of_sale_name
from arca_integration.arca.mtxca import get_mtxca_client, get_wscdc_client
from arca_integration.services.endpoints import Environment
from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.catalogs import group_receiver_conditions, is_issuable, vat_rate
from arca_integration.services.mtxca.client import (
	ERROR_INVALID_VOUCHER_TYPE,
	CodeDescription,
	MtxcaClient,
	MtxcaError,
	PointOfSale,
)

TEST_POINT_OF_SALE = 1
SEEDS_DIR = Path(__file__).parent / "seeds"

# DocType -> MtxcaClient method that returns its codes.
CODE_CATALOGS: dict[str, str] = {
	"ARCA Voucher Type": "voucher_types",
	"ARCA Document Type": "document_types",
	"ARCA VAT Condition": "vat_conditions",
	"ARCA Tax Type": "tax_types",
	"ARCA Unit of Measure": "units_of_measure",
	"ARCA Currency": "currencies",
}

EXTRA_FIELDS: dict[str, Callable[[CodeDescription], dict]] = {
	"ARCA VAT Condition": lambda entry: {"rate": vat_rate(entry.description) or 0},
}


@dataclass
class SyncResult:
	added: int = 0
	updated: int = 0
	disabled: int = 0

	def as_dict(self) -> dict:
		return {"added": self.added, "updated": self.updated, "disabled": self.disabled}


def upsert_codes(
	doctype: str, entries: Iterable[CodeDescription], extra=None, disable_missing: bool = True
) -> SyncResult:
	"""Make `doctype` match `entries`: insert new codes, update changed ones, disable missing ones."""
	extra = extra or EXTRA_FIELDS.get(doctype) or (lambda entry: {})
	result = SyncResult()
	seen = set()

	for entry in entries:
		seen.add(entry.code)
		values = {"description": entry.description, "disabled": 0, **extra(entry)}
		if frappe.db.exists(doctype, entry.code):
			doc = frappe.get_doc(doctype, entry.code)
			if _apply(doc, values):
				doc.save(ignore_permissions=True)
				result.updated += 1
		else:
			frappe.get_doc({"doctype": doctype, "code": entry.code, **values}).insert(ignore_permissions=True)
			result.added += 1

	for name in (
		frappe.get_all(doctype, filters={"disabled": 0, "name": ["not in", list(seen) or [""]]}, pluck="name")
		if disable_missing
		else []
	):
		frappe.db.set_value(doctype, name, "disabled", 1)
		result.disabled += 1

	return result


def _apply(doc, values: dict) -> bool:
	"""Set `values` on `doc` and tell whether anything changed. Child tables compare by content."""
	changed = False
	for field, value in values.items():
		if isinstance(value, list):
			current = (
				[{k: row.get(k) for k in value[0]} for row in doc.get(field)] if value else doc.get(field)
			)
			if current != value:
				doc.set(field, value)
				changed = True
		elif doc.get(field) != value:
			doc.set(field, value)
			changed = True
	return changed


def sync_receiver_conditions(client: MtxcaClient) -> SyncResult:
	by_voucher_type = {}
	for voucher_type in client.voucher_types():
		if not is_issuable(voucher_type):
			continue
		try:
			by_voucher_type[voucher_type.code] = client.receiver_vat_conditions(voucher_type.code)
		except MtxcaError as exc:
			if exc.codes != {ERROR_INVALID_VOUCHER_TYPE}:
				raise

	conditions = group_receiver_conditions(by_voucher_type)
	types_by_code = {c.code: [{"voucher_type": vt} for vt in c.voucher_types] for c in conditions}
	return upsert_codes(
		"ARCA Receiver VAT Condition",
		[CodeDescription(c.code, c.description) for c in conditions],
		extra=lambda entry: {"voucher_types": types_by_code[entry.code]},
	)


def sync_catalogs(company: str) -> dict[str, SyncResult]:
	"""Sync every code catalog using `company`'s credentials. The catalogs are global."""
	client = get_mtxca_client(company)
	results = {
		doctype: upsert_codes(doctype, getattr(client, method)())
		for doctype, method in CODE_CATALOGS.items()
		if doctype != "ARCA Voucher Type"
	}
	results["ARCA Voucher Type"] = sync_voucher_types(company, client)
	results["ARCA Receiver VAT Condition"] = sync_receiver_conditions(client)
	link_standard_records()
	_show_events(client)
	return results


def sync_voucher_types(company: str, client: MtxcaClient) -> SyncResult:
	"""The types the company issues (WSMTXCA) and the ones it may receive (WSCDC).

	Without WSCDC, nothing is disabled: the received types would look obsolete.
	"""
	types = {entry.code: entry for entry in client.voucher_types()}
	try:
		received = get_wscdc_client(company).voucher_types()
	except (ArcaError, frappe.ValidationError):
		frappe.clear_last_message()
		return upsert_codes("ARCA Voucher Type", types.values(), disable_missing=False)
	for entry in received:
		types.setdefault(entry.code, entry)
	return upsert_codes("ARCA Voucher Type", types.values())


# Standard ERPNext records and the ARCA code they stand for.
STANDARD_LINKS = {
	("Currency", "arca_currency", "ARCA Currency"): {
		"ARS": "PES",
		"USD": "DOL",
		"EUR": "060",
		"BRL": "012",
		"GBP": "021",
		"JPY": "019",
		"UYU": "011",
		"MXN": "010",
		"CAD": "018",
		"CLP": "033",
	},
	("UOM", "arca_unit_of_measure", "ARCA Unit of Measure"): {
		"Nos": "7",
		"Unit": "7",
		"Kg": "1",
		"Gram": "14",
		"Litre": "5",
		"Millilitre": "47",
		"Meter": "2",
		"Centimeter": "20",
		"Kilometer": "17",
		"Square Meter": "3",
		"Cubic Meter": "4",
		"Pair": "8",
	},
}


def link_standard_records() -> None:
	"""Link standard currencies and units to their ARCA code, where nobody set one yet."""
	for (doctype, fieldname, catalog), links in STANDARD_LINKS.items():
		for name, code in links.items():
			if (
				frappe.db.exists(doctype, name)
				and frappe.db.exists(catalog, code)
				and not frappe.db.get_value(doctype, name, fieldname)
			):
				frappe.db.set_value(doctype, name, fieldname, code, update_modified=False)


def sync_points_of_sale(company: str, points: list[PointOfSale] | None = None) -> SyncResult:
	"""Sync the CAE points of sale of `company` in its active environment.

	`points` lets a caller that already queried ARCA reuse the answer.
	"""
	environment = frappe.db.get_value("ARCA Company Settings", company, "environment")
	client = None
	if points is None:
		client = get_mtxca_client(company)
		points = client.points_of_sale_cae()
	result = SyncResult()
	seen = set()

	for point in points:
		name = point_of_sale_name(company, environment, point.number)
		seen.add(name)
		values = {"blocked": int(point.blocked), "deactivated_on": point.deactivated_on, "disabled": 0}
		if frappe.db.exists("ARCA Point of Sale", name):
			doc = frappe.get_doc("ARCA Point of Sale", name)
			if _apply(doc, values):
				doc.save(ignore_permissions=True)
				result.updated += 1
		else:
			frappe.get_doc(
				{
					"doctype": "ARCA Point of Sale",
					"company": company,
					"environment": environment,
					"number": point.number,
					**values,
				}
			).insert(ignore_permissions=True)
			result.added += 1

	if not points and environment == Environment.HOMOLOGATION:
		# Homologation lists none; keep the test point of sale.
		return result

	stale = frappe.get_all(
		"ARCA Point of Sale",
		filters={
			"company": company,
			"environment": environment,
			"disabled": 0,
			"name": ["not in", list(seen) or [""]],
		},
		pluck="name",
	)
	for name in stale:
		frappe.db.set_value("ARCA Point of Sale", name, "disabled", 1)
		result.disabled += 1

	if client:
		_show_events(client)
	return result


def create_test_point_of_sale(company: str, number: int = TEST_POINT_OF_SALE):
	"""Point of sale used in homologation, where ARCA accepts any number."""
	name = point_of_sale_name(company, Environment.HOMOLOGATION, number)
	if frappe.db.exists("ARCA Point of Sale", name):
		doc = frappe.get_doc("ARCA Point of Sale", name)
		if doc.disabled:
			doc.db_set("disabled", 0)
		return doc
	return frappe.get_doc(
		{
			"doctype": "ARCA Point of Sale",
			"company": company,
			"environment": Environment.HOMOLOGATION,
			"number": number,
		}
	).insert(ignore_permissions=True)


def _show_events(client: MtxcaClient) -> None:
	for event in client.events:
		frappe.msgprint(event.description, title=_("ARCA Announcement"), indicator="blue", alert=True)


@frappe.whitelist()
def sync_now(company: str) -> dict:
	"""Button in ARCA Settings: sync the catalogs and the company's points of sale."""
	frappe.only_for("System Manager")
	try:
		results = sync_catalogs(company)
		results["ARCA Point of Sale"] = sync_points_of_sale(company)
	except MtxcaError as exc:
		frappe.throw(_("ARCA rejected the request: {0}").format(exc.message), title=_("ARCA Sync"))
	except ArcaError as exc:
		frappe.throw(_("Could not connect to ARCA: {0}").format(exc.message), title=_("ARCA Sync"))
	return {doctype: result.as_dict() for doctype, result in results.items()}


def scheduled_sync() -> None:
	"""Daily job: points of sale for every configured company; catalogs once, with the first company that works."""
	catalogs_done = False
	for company in frappe.get_all("ARCA Company Settings", pluck="company"):
		try:
			if not catalogs_done:
				sync_catalogs(company)
				catalogs_done = True
			sync_points_of_sale(company)
			frappe.db.commit()  # nosemgrep: keep each company's progress if a later one fails
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"ARCA catalog sync failed for {company}")


def seed_catalogs() -> None:
	"""Insert the seed codes that are still missing. Runs after install and after every migrate."""
	for doctype in CODE_CATALOGS:
		path = SEEDS_DIR / f"{frappe.scrub(doctype)}.json"
		if not path.exists():
			continue
		extra = EXTRA_FIELDS.get(doctype) or (lambda entry: {})
		for row in json.loads(path.read_text()):
			if not frappe.db.exists(doctype, row["code"]):
				entry = CodeDescription(row["code"], row["description"])
				frappe.get_doc({"doctype": doctype, **row, **extra(entry)}).insert(ignore_permissions=True)
