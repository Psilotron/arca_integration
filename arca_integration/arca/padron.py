"""'Complete from ARCA': look up a CUIT in the padrón and propose the values to apply."""

import json

import frappe
from frappe import _

from arca_integration.arca.connection import problem_message
from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import obtain_ticket
from arca_integration.arca.doctype.arca_company_settings.arca_company_settings import get_company_settings
from arca_integration.arca.doctype.arca_settings.arca_settings import get_endpoint, get_transport
from arca_integration.services.credentials import is_valid_cuit, normalize_cuit
from arca_integration.services.diagnosis import classify
from arca_integration.services.endpoints import WSAA_SERVICE_IDS, Service
from arca_integration.services.errors import ArcaError
from arca_integration.services.mtxca.client import Auth
from arca_integration.services.padron import PadronClient, Taxpayer, TaxpayerNotFound

PADRON_SERVICE_ID = WSAA_SERVICE_IDS[Service.PADRON_A5]


def resolve_company(company: str | None = None) -> str:
	"""Company whose certificate queries the padrón: the given one, the user's default, or the only one set up."""
	configured = frappe.get_all("ARCA Company Settings", pluck="name")
	if company:
		if company not in configured:
			frappe.throw(_("Set up ARCA Company Settings for {0}.").format(company))
		return company

	default = frappe.defaults.get_user_default("Company")
	if default in configured:
		return default
	if len(configured) == 1:
		return configured[0]
	if not configured:
		frappe.throw(_("Set up ARCA first: there is no company with ARCA settings."))
	frappe.throw(_("Choose which company's certificate to use for the lookup."))


def get_padron_client(company: str) -> PadronClient:
	settings = get_company_settings(company)
	ticket = obtain_ticket(company, Service.PADRON_A5)
	return PadronClient(
		url=get_endpoint(Service.PADRON_A5, settings.environment),
		transport=get_transport(),
		auth=Auth(token=ticket.token, sign=ticket.sign, cuit=settings.cuit),
	)


def taxpayer_proposal(taxpayer: Taxpayer) -> dict:
	"""Values the dialog offers to apply, plus warnings to show."""
	warnings = []
	if taxpayer.constancia_error:
		warnings.append(
			_("ARCA does not issue this taxpayer's constancia: {0}").format(taxpayer.constancia_error)
		)
	elif taxpayer.key_status and not taxpayer.is_active:
		warnings.append(
			_(
				"The CUIT is {0} in ARCA. ARCA rejects invoices of 10,000,000 or more to inactive CUITs."
			).format(taxpayer.key_status)
		)

	vat_condition = taxpayer.suggested_vat_condition if not taxpayer.constancia_error else None
	if vat_condition and not frappe.db.exists("ARCA Receiver VAT Condition", vat_condition):
		warnings.append(_("Sync the ARCA catalogs to apply the VAT condition."))
		vat_condition = None
	address = taxpayer.address
	return {
		"cuit": taxpayer.cuit,
		"name": taxpayer.name,
		"customer_type": "Company" if taxpayer.is_company else "Individual",
		"document_type": taxpayer.document_type,
		"vat_condition": vat_condition,
		"vat_condition_label": frappe.db.get_value(
			"ARCA Receiver VAT Condition", vat_condition, "description"
		)
		if vat_condition
		else None,
		"address": {
			"address_line1": address.line1,
			"city": address.city,
			"state": address.province,
			"pincode": address.postal_code,
		}
		if address and address.line1
		else None,
		"activities": list(taxpayer.activities),
		"warnings": warnings,
	}


@frappe.whitelist()
def lookup(cuit: str, company: str | None = None) -> dict:
	cuit = normalize_cuit(cuit)
	if not is_valid_cuit(cuit):
		frappe.throw(_("{0} is not a valid CUIT.").format(cuit), title=_("ARCA Padrón"))

	company = resolve_company(company)
	try:
		taxpayer = get_padron_client(company).get_taxpayer(cuit)
	except TaxpayerNotFound:
		frappe.throw(_("ARCA has no taxpayer with CUIT {0}.").format(cuit), title=_("ARCA Padrón"))
	except ArcaError as exc:
		settings = get_company_settings(company)
		what, todo = problem_message(
			classify(exc), settings.environment, settings.cuit, exc.message, service=PADRON_SERVICE_ID
		)
		frappe.throw(f"{what}<br>{todo}", title=_("ARCA Padrón"))
	return taxpayer_proposal(taxpayer)


@frappe.whitelist()
def apply_address(link_doctype: str, link_name: str, address: dict | str) -> str:
	"""Create or update the primary billing address of `link_name` with the padrón's fiscal address."""
	if link_doctype not in ("Customer", "Company"):
		frappe.throw(_("Addresses from ARCA can only be applied to customers and companies."))
	frappe.get_doc(link_doctype, link_name).check_permission("write")
	values = json.loads(address) if isinstance(address, str) else address

	existing = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": link_doctype, "link_name": link_name, "parenttype": "Address"},
		"parent",
	)
	is_billing = existing and frappe.db.get_value("Address", existing, "address_type") == "Billing"
	doc = frappe.get_doc("Address", existing) if is_billing else frappe.new_doc("Address")
	if doc.is_new():
		doc.update(
			{
				"address_title": link_name,
				"address_type": "Billing",
				"is_primary_address": 1,
				"links": [{"link_doctype": link_doctype, "link_name": link_name}],
				"is_your_company_address": int(link_doctype == "Company"),
			}
		)
	doc.update(
		{
			"address_line1": values.get("address_line1"),
			"city": values.get("city"),
			"state": values.get("state"),
			"pincode": values.get("pincode"),
			"country": "Argentina",
		}
	)
	doc.save()
	return doc.name
