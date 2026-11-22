"""Demo data for ARCA homologation: a site ready to issue test invoices.

`bench --site <site> arca-demo --cert <crt> --key <key>` (see commands.py)
completes the setup wizard on a new site, creates a company with the given
homologation certificate, the accounts and tax templates for VAT, a few items,
customers taken from ARCA's homologation registry, and verifies the
connection. It never runs in production and never stores credentials in code:
the CUIT comes from the certificate.

Running it again completes what is missing; it does not duplicate records.
"""

from datetime import date
from pathlib import Path

import frappe

from arca_integration.services.credentials import read_certificate

DEFAULT_COMPANY = "Demo ARCA"
DEFAULT_ABBR = "DARCA"
TIME_ZONE = "America/Argentina/Buenos_Aires"

# Test CUITs that ARCA publishes for its homologation registry
# (https://www.afip.gob.ar/ws/ws_sr_padron_a4/datos-prueba-padron-a4.txt).
# Their VAT condition is whatever the registry answers when the demo runs.
TEST_CUITS = (
	"30558515305",
	"30558521135",
	"30558564675",
	"30202020204",
	"20002460123",
	"20002307554",
	"20188192514",
	"20221062583",
)
# Generic product code used in the WSMTXCA manual's examples.
DEMO_GTIN = "7790001001139"


class DemoError(Exception):
	pass


def setup_demo(
	cert_path: str,
	key_path: str,
	company: str = DEFAULT_COMPANY,
	abbr: str = DEFAULT_ABBR,
	force: bool = False,
	invoices: bool = True,
	tickets: str | None = None,
) -> list[str]:
	"""Build the demo and return what was done, one line per step."""
	report: list[str] = []
	certificate = Path(cert_path).read_text()
	private_key = Path(key_path).read_text()
	cuit = read_certificate(certificate).cuit
	if not cuit:
		raise DemoError("The certificate has no CUIT in its subject (serialNumber 'CUIT nnnnnnnnnnn').")

	_check_safe(company, force)
	frappe.set_user("Administrator")

	_setup_site(company, abbr, cuit, report)
	abbr = frappe.db.get_value("Company", company, "abbr")
	_setup_arca(company, cuit, certificate, private_key, report)
	if tickets:
		report.append(f"WSAA tickets restored: {import_tickets(tickets)}.")
	accounts = _setup_accounts(company, abbr, report)
	templates = _setup_tax_templates(company, abbr, accounts, report)
	items = _setup_items(templates, report)
	_verify_connection(company, report)
	frappe.db.set_value("Account", accounts["perception"], "arca_tax_type", "7")
	customers = _setup_customers(company, report)
	if invoices:
		_setup_invoices(company, abbr, accounts, items, customers, report)
	frappe.db.commit()
	return report


def _check_safe(company: str, force: bool) -> None:
	settings = frappe.db.get_value("ARCA Company Settings", company, "environment")
	if settings == "Production":
		raise DemoError(f"{company} is set up for ARCA production. The demo only runs in homologation.")
	if not force and frappe.db.exists(
		"Sales Invoice", {"arca_cae": ["is", "set"], "company": ["!=", company]}
	):
		raise DemoError(
			"This site has invoices authorized by ARCA for other companies. Use a new site, or --force."
		)


# ---- Site and company ----


def _setup_site(company: str, abbr: str, cuit: str, report: list[str]) -> None:
	if not frappe.is_setup_complete():
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		year = date.today().year
		result = setup_complete(
			{
				"language": "English",  # Spanish is set below: the wizard expects a language name
				"country": "Argentina",
				"timezone": TIME_ZONE,
				"currency": "ARS",
				"company_name": company,
				"company_abbr": abbr,
				"chart_of_accounts": "Standard",
				"fy_start_date": f"{year}-01-01",
				"fy_end_date": f"{year}-12-31",
				"setup_demo": 0,
			}
		)
		if not frappe.is_setup_complete():
			raise DemoError(f"The setup wizard did not complete: {result}")
		report.append(f"Setup wizard completed: {company}, Argentina, ARS.")

	frappe.db.set_single_value(
		"System Settings",
		{
			"time_zone": TIME_ZONE,
			"language": "es",
			"country": "Argentina",
			"currency": "ARS",
			"date_format": "dd-mm-yyyy",
			"number_format": "#.###,##",
		},
	)
	frappe.db.set_value("User", "Administrator", "language", "es")

	if not frappe.db.exists("Company", company):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company,
				"abbr": abbr,
				"default_currency": "ARS",
				"country": "Argentina",
				"chart_of_accounts": "Standard",
			}
		).insert()
		report.append(f"Company created: {company}.")
	frappe.db.set_value("Company", company, "tax_id", f"{cuit[:2]}-{cuit[2:10]}-{cuit[10:]}")


def _setup_arca(company: str, cuit: str, certificate: str, private_key: str, report: list[str]) -> None:
	if frappe.db.exists("ARCA Company Settings", company):
		settings = frappe.get_doc("ARCA Company Settings", company)
	else:
		settings = frappe.new_doc("ARCA Company Settings")
		settings.company = company
	settings.update(
		{
			"cuit": cuit,
			"environment": "Homologation",
			"homologation_certificate": certificate,
			"homologation_private_key": private_key,
			"default_vat_condition": "5",
		}
	)
	settings.save()
	report.append(f"ARCA set up for CUIT {cuit} in homologation.")


# ---- Accounting ----


def _account(company: str, abbr: str, name: str, parent_type: str, **values) -> str:
	full_name = f"{name} - {abbr}"
	if not frappe.db.exists("Account", full_name):
		parent = frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "account_type": parent_type}, "name"
		) or frappe.db.get_value("Account", {"company": company, "is_group": 1, "root_type": "Liability"})
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": name,
				"parent_account": parent,
				"company": company,
				**values,
			}
		).insert()
	else:
		frappe.db.set_value("Account", full_name, values)
	return full_name


def _setup_accounts(company: str, abbr: str, report: list[str]) -> dict:
	accounts = {
		"vat": _account(company, abbr, "IVA Débito Fiscal", "Tax", account_type="Tax", arca_is_vat=1),
		"perception": _account(company, abbr, "Percepción IIBB a Pagar", "Tax", account_type="Tax"),
	}
	report.append("Accounts: IVA Débito Fiscal (VAT for ARCA) and Percepción IIBB a Pagar.")
	return accounts


def _setup_tax_templates(company: str, abbr: str, accounts: dict, report: list[str]) -> dict:
	templates = {}
	for key, title, rate, condition in (
		("21", "IVA 21%", 21, "5"),
		("10.5", "IVA 10,5%", 10.5, "4"),
		("exempt", "IVA Exento", 0, "2"),
	):
		name = f"{title} - {abbr}"
		if not frappe.db.exists("Item Tax Template", name):
			frappe.get_doc(
				{
					"doctype": "Item Tax Template",
					"title": title,
					"company": company,
					"arca_vat_condition": condition,
					"taxes": [{"tax_type": accounts["vat"], "tax_rate": rate}],
				}
			).insert()
		templates[key] = name

	sales_template = f"IVA Ventas - {abbr}"
	if not frappe.db.exists("Sales Taxes and Charges Template", sales_template):
		frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": "IVA Ventas",
				"company": company,
				"is_default": 1,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": accounts["vat"],
						"description": "IVA",
						"rate": 21,
					}
				],
			}
		).insert()
	templates["sales"] = sales_template
	report.append("Item tax templates: IVA 21%, IVA 10,5%, IVA Exento; sales template IVA Ventas.")
	return templates


def _setup_items(templates: dict, report: list[str]) -> dict:
	items = {}
	for code, name, stock, template, rate, barcode in (
		("DEMO-PRODUCTO", "Producto de prueba", 1, "21", 10000, DEMO_GTIN),
		("DEMO-ALIMENTO", "Alimento de prueba (10,5 %)", 1, "10.5", 5000, None),
		("DEMO-SERVICIO", "Servicio de prueba", 0, "21", 20000, None),
		("DEMO-EXENTO", "Libro de prueba (exento)", 1, "exempt", 3000, None),
	):
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": name,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": stock,
					"standard_rate": rate,
					"taxes": [{"item_tax_template": templates[template]}],
					"barcodes": [{"barcode": barcode, "barcode_type": "EAN"}] if barcode else [],
				}
			).insert()
		items[code] = rate
	report.append(f"Items: {', '.join(items)}.")
	return items


# ---- ARCA ----


def _verify_connection(company: str, report: list[str]) -> None:
	from arca_integration.arca.connection import verify_connection

	result = verify_connection(company)
	for step in result["steps"]:
		report.append(
			f"  [{step['status']}] {step['title']}: {step['message']} {step.get('action') or ''}".rstrip()
		)
	if result["status"] != "Ready":
		raise DemoError("The ARCA connection is not ready; see the steps above.")
	report.append("ARCA connection ready: catalogs and point of sale synced.")


def _setup_customers(company: str, report: list[str]) -> list[str]:
	from arca_integration.arca.padron import apply_address, lookup

	customers = []
	for cuit in TEST_CUITS:
		existing = frappe.db.get_value("Customer", {"tax_id": cuit}, "name")
		if existing:
			customers.append(existing)
			continue
		try:
			proposal = lookup(cuit, company)
		except frappe.ValidationError as exc:
			message = frappe.utils.strip_html(str(exc).replace("<br>", " "))
			report.append(f"  CUIT {cuit} skipped: {message}")
			frappe.clear_messages()
			continue
		# The homologation registry often has no VAT data. Assigning one would be invented,
		# and ARCA observes A invoices to a CUIT it does not have as VAT registered (130).
		condition = proposal["vat_condition"]
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": proposal["name"] or f"Contribuyente {cuit}",
				"customer_type": proposal["customer_type"],
				"tax_id": cuit,
				"arca_document_type": proposal["document_type"] or "80",
				"arca_receiver_vat_condition": condition,
			}
		).insert()
		if proposal.get("address") and proposal["address"].get("address_line1"):
			apply_address("Customer", customer.name, proposal["address"])
		customers.append(customer.name)
		label = proposal["vat_condition_label"] or "no VAT condition in the registry: complete it by hand"
		report.append(f"  {customer.name} ({cuit}): {label}")

	if not frappe.db.exists("Customer", "Consumidor Final"):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Consumidor Final",
				"customer_type": "Individual",
				"arca_receiver_vat_condition": "5",
			}
		).insert()
	customers.append("Consumidor Final")
	report.append(f"Customers: {len(customers)}, from ARCA's homologation registry plus Consumidor Final.")
	return customers


def _setup_invoices(company, abbr, accounts, items, customers, report) -> None:
	"""One draft per kind: an A (if a VAT registered customer exists), a B for services, a B mixed."""
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	conditions = {
		name: frappe.db.get_value("Customer", name, "arca_receiver_vat_condition") for name in customers
	}
	registered = next((name for name, condition in conditions.items() if condition in ("1", "6")), None)
	drafts = [
		(registered, ["DEMO-PRODUCTO", "DEMO-ALIMENTO"]),
		("Consumidor Final", ["DEMO-SERVICIO"]),
		("Consumidor Final", ["DEMO-PRODUCTO", "DEMO-EXENTO"]),
	]
	if frappe.db.exists("Sales Invoice", {"company": company, "docstatus": 0}):
		report.append("Draft invoices: the company already has drafts, none added.")
		return
	created = 0
	for customer, codes in drafts:
		if not customer:
			report.append(
				"  No customer is VAT registered in the registry: skipped the A invoice. "
				"Set a customer's VAT condition by hand to try one (ARCA will observe it with code 130)."
			)
			continue
		invoice = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": company,
				"customer": customer,
				"currency": "ARS",
				"posting_date": date.today(),
				"due_date": date.today(),
				"taxes_and_charges": f"IVA Ventas - {abbr}",
				"items": [{"item_code": code, "qty": 1, "rate": items[code]} for code in codes],
			}
		)
		invoice.set(
			"taxes", get_taxes_and_charges("Sales Taxes and Charges Template", f"IVA Ventas - {abbr}")
		)
		invoice.insert()
		created += 1
	report.append(f"Draft invoices: {created}, ready to submit and request a test CAE.")


# ---- WSAA tickets across reinstalls ----


def export_tickets(path: str) -> int:
	"""Save valid WSAA tickets, so a reinstall does not block logins for up to 12 hours."""
	import json

	from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import DocTicketStore

	rows = []
	for ticket in frappe.get_all("ARCA Access Ticket", fields=["company", "environment", "service"]):
		stored = DocTicketStore(ticket.company, ticket.environment, ticket.service).get()
		cuit = frappe.db.get_value("ARCA Company Settings", ticket.company, "cuit")
		if stored and cuit and len(stored.token) > 100:
			rows.append(
				{
					"cuit": cuit,
					"environment": ticket.environment,
					"service": ticket.service,
					"token": stored.token,
					"sign": stored.sign,
					"expires_at": stored.expires_at.isoformat(),
				}
			)
	target = Path(path)
	target.write_text(json.dumps(rows, indent=1))
	target.chmod(0o600)
	return len(rows)


def import_tickets(path: str) -> int:
	"""Restore tickets saved by `export_tickets` into the company that now has the same CUIT."""
	import json
	from datetime import UTC, datetime

	from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import DocTicketStore
	from arca_integration.services.wsaa import Ticket

	restored = 0
	for row in json.loads(Path(path).read_text()):
		ticket = Ticket(row["token"], row["sign"], datetime.fromisoformat(row["expires_at"]))
		if not ticket.is_valid(datetime.now(UTC)):
			continue
		# The ticket belongs to the certificate: every company using its CUIT shares it.
		for company in frappe.get_all(
			"ARCA Company Settings",
			filters={"cuit": row["cuit"], "environment": row["environment"]},
			pluck="name",
		):
			DocTicketStore(company, row["environment"], row["service"]).set(ticket)
			restored += 1
	return restored
