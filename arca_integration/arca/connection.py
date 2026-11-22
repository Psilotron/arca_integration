"""Guided connection check for a company: each step is verified against ARCA.

The result is a list of steps, each with a status and, when something fails, a
plain-language explanation and the action that fixes it. It is stored on ARCA
Company Settings and drawn as a checklist by its form script.
"""

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from arca_integration.arca.catalogs import create_test_point_of_sale, sync_catalogs, sync_points_of_sale
from arca_integration.arca.doctype.arca_access_ticket.arca_access_ticket import obtain_ticket
from arca_integration.arca.doctype.arca_company_settings.arca_company_settings import get_company_settings
from arca_integration.arca.mtxca import get_status_client, mtxca_client_for
from arca_integration.services.diagnosis import CERTIFICATE_WARNING_DAYS, Problem, classify, days_until
from arca_integration.services.endpoints import WSAA_SERVICE_IDS, Environment, Service
from arca_integration.services.errors import ArcaError

OK, WARNING, ERROR, SKIPPED = "ok", "warning", "error", "skipped"
READY, NEEDS_ATTENTION = "Ready", "Needs Attention"

# Onboarding steps completed by a successful check.
ONBOARDING_STEPS = {
	"settings": "ARCA Company",
	"login": "ARCA Certificate",
	"points_of_sale": "ARCA Point of Sale",
	"all": "ARCA Connection",
}


def problem_message(
	problem: Problem, environment: str, cuit: str, raw: str = "", service: str = "wsmtxca"
) -> tuple[str, str]:
	"""(what happened, what to do) for a classified failure, in the user's language."""
	homologation = environment == Environment.HOMOLOGATION
	messages = {
		Problem.ARCA_UNREACHABLE: (
			_("Could not reach ARCA."),
			_("Check this server's internet connection and try again in a few minutes."),
		),
		Problem.ARCA_UNAVAILABLE: (
			_("ARCA reports that the service is not available right now."),
			_("The problem is on ARCA's side. Try again later."),
		),
		Problem.CERTIFICATE_EXPIRED: (
			_("The certificate has expired."),
			_("Generate a new key and CSR, request a new certificate in ARCA and paste it here."),
		),
		Problem.CERTIFICATE_NOT_TRUSTED: (
			_("ARCA does not recognize this certificate for {0}.").format(_(environment)),
			_(
				"Homologation certificates do not work in production, and vice versa. Paste the certificate ARCA issued for this environment."
			),
		),
		Problem.SERVICE_NOT_AUTHORIZED: (
			_("The certificate is not authorized for the {0} service.").format(service),
			_(
				"In WSASS, create an authorization for the {0} service using this certificate's computer name."
			).format(service)
			if homologation
			else _(
				"On the ARCA website, open 'Administrador de Relaciones de Clave Fiscal', add a relation for the {0} web service and assign it to this certificate's computer name."
			).format(service),
		),
		Problem.CUIT_NOT_RELATED: (
			_("ARCA does not find CUIT {0} among the relations of this certificate.").format(cuit),
			_(
				"If the certificate belongs to another CUIT (for example, your accountant's), the company must delegate the {0} service to that CUIT in 'Administrador de Relaciones de Clave Fiscal'."
			).format(service),
		),
		Problem.ALREADY_AUTHENTICATED: (
			_("ARCA already issued an access ticket for this certificate that this site did not keep."),
			_("Wait until it expires (up to 12 hours) or use a new certificate."),
		),
		Problem.CLOCK_SKEW: (
			_("ARCA rejected the time of the request."),
			_("This server's clock is wrong. Ask whoever manages the server to synchronize it (NTP)."),
		),
	}
	return messages.get(
		problem,
		(_("ARCA answered: {0}").format(raw), _("If the message is not clear, share it with support.")),
	)


def _step(
	key: str,
	title: str,
	status: str,
	message: str = "",
	action: str = "",
	detail: str = "",
	optional: bool = False,
) -> dict:
	"""One checklist row. Optional steps never keep the company from being Ready."""
	return {
		"key": key,
		"title": title,
		"status": status,
		"message": message,
		"action": action,
		"detail": detail,
		"optional": optional,
	}


def _failed(key: str, title: str, error: Exception, settings, service: str = "wsmtxca") -> dict:
	problem = classify(error)
	what, todo = problem_message(
		problem, settings.environment, settings.cuit, getattr(error, "message", str(error)), service
	)
	detail = f"{type(error).__name__}: {error}"
	if getattr(error, "detail", None):
		detail += f" ({error.detail})"
	return _step(key, title, ERROR, what, todo, detail=detail)


def check_settings(settings) -> dict:
	title = _("Company data and certificate")
	environment = settings.environment
	prefix = environment.lower()
	certificate = settings.get(f"{prefix}_certificate")

	if not certificate or not settings.get_private_key(environment):
		return _step(
			"settings",
			title,
			ERROR,
			_("There is no certificate for {0}.").format(_(environment)),
			_("Use 'Generate Key and CSR', request the certificate in ARCA and paste it in {0}.").format(
				_("{0} Credentials").format(_(environment))
			),
		)

	expiry = get_datetime(settings.get(f"{prefix}_certificate_expiry"))
	days = days_until(expiry, now_datetime())
	if days < 0:
		return _step(
			"settings",
			title,
			ERROR,
			_("The certificate expired on {0}.").format(frappe.format(expiry, "Datetime")),
			_("Generate a new key and CSR, request a new certificate in ARCA and paste it here."),
		)
	if days <= CERTIFICATE_WARNING_DAYS:
		return _step(
			"settings",
			title,
			WARNING,
			_("The certificate expires in {0} days.").format(days),
			_("Renew it before it expires: generate a new key and CSR and request the certificate in ARCA."),
		)
	return _step(
		"settings", title, OK, _("Certificate valid until {0}.").format(frappe.format(expiry, "Date"))
	)


def choose_point_of_sale(settings) -> dict:
	title = _("Point of sale")
	usable = frappe.get_all(
		"ARCA Point of Sale",
		filters={
			"company": settings.company,
			"environment": settings.environment,
			"disabled": 0,
			"blocked": 0,
			"deactivated_on": ["is", "not set"],
		},
		fields=["name", "number"],
		order_by="number asc",
	)
	listed = frappe.db.exists(
		"ARCA Point of Sale",
		{"company": settings.company, "environment": settings.environment, "disabled": 0},
	)
	if not listed and settings.environment == Environment.HOMOLOGATION:
		# Homologation lists no points of sale and accepts any number.
		point = create_test_point_of_sale(settings.company)
		settings.db_set("default_point_of_sale", point.name, update_modified=False)
		return _step(
			"points_of_sale",
			title,
			OK,
			_(
				"Homologation does not require registering points of sale. Point of sale {0} is used for tests."
			).format(f"{point.number:05d}"),
		)
	if not usable:
		return _step(
			"points_of_sale",
			title,
			ERROR,
			_("ARCA has no active point of sale for invoices with item detail for this CUIT."),
			_(
				"On the ARCA website, open 'Administración de puntos de venta y domicilios' and create a point of sale for 'Factura electrónica - Codificación de productos - Web services'. Use a new number, not shared with other systems."
			),
		)

	names = {point.name for point in usable}
	if settings.default_point_of_sale not in names:
		if len(usable) > 1:
			return _step(
				"points_of_sale",
				title,
				WARNING,
				_("ARCA has {0} active points of sale.").format(len(usable)),
				_("Choose the one this company uses in 'Default Point of Sale'."),
			)
		settings.db_set("default_point_of_sale", usable[0].name, update_modified=False)

	number = next(point.number for point in usable if point.name == settings.default_point_of_sale)
	return _step("points_of_sale", title, OK, _("Point of sale {0} is active.").format(f"{number:05d}"))


def run_checks(company: str) -> list[dict]:
	settings = get_company_settings(company)
	steps = [check_settings(settings)]
	failed = steps[0]["status"] == ERROR

	def skip(key, title):
		steps.append(_step(key, title, SKIPPED, _("Fix the previous step first.")))

	# 1. ARCA is up (needs no credentials)
	title = _("ARCA service")
	try:
		status = get_status_client(company).dummy()
		if {status.app_server, status.db_server, status.auth_server} == {"OK"}:
			steps.append(_step("arca", title, OK, _("ARCA is answering.")))
		else:
			steps.append(
				_step(
					"arca",
					title,
					WARNING,
					_(
						"ARCA reports problems in its servers (application: {0}, database: {1}, authentication: {2})."
					).format(status.app_server, status.db_server, status.auth_server),
					_("The problem is on ARCA's side. Try again later."),
				)
			)
	except ArcaError as exc:
		steps.append(_failed("arca", title, exc, settings))
		failed = True

	# 2. WSAA accepts the certificate for wsmtxca
	title = _("Certificate authorization (WSAA)")
	ticket = None
	if failed:
		skip("login", title)
	else:
		try:
			ticket = obtain_ticket(company, Service.WSMTXCA)
			steps.append(_step("login", title, OK, _("ARCA accepted the certificate for wsmtxca.")))
		except ArcaError as exc:
			steps.append(_failed("login", title, exc, settings))
			failed = True

	# 3. MTXCA answers for this CUIT, and lists its points of sale
	title = _("Invoices with item detail (WSMTXCA)")
	points = None
	if failed:
		skip("service", title)
	else:
		try:
			points = mtxca_client_for(company, ticket).points_of_sale_cae()
			steps.append(
				_step("service", title, OK, _("ARCA lets CUIT {0} use wsmtxca.").format(settings.cuit))
			)
		except ArcaError as exc:
			steps.append(_failed("service", title, exc, settings))
			failed = True

	# 4. Points of sale
	if failed:
		skip("points_of_sale", _("Point of sale"))
	else:
		sync_points_of_sale(company, points)
		settings.reload()
		steps.append(choose_point_of_sale(settings))

	# 5. Catalogs
	title = _("ARCA codes")
	if failed:
		skip("catalogs", title)
	elif frappe.db.count("ARCA Receiver VAT Condition") and frappe.db.count("ARCA Document Type"):
		steps.append(_step("catalogs", title, OK, _("Codes are up to date.")))
	else:
		try:
			sync_catalogs(company)
			steps.append(_step("catalogs", title, OK, _("Codes downloaded from ARCA.")))
		except ArcaError as exc:
			steps.append(_failed("catalogs", title, exc, settings))

	# 6. Padrón lookup (optional: only for "Complete from ARCA")
	title = _("CUIT lookup in the ARCA registry (optional)")
	if failed:
		skip("padron", title)
		steps[-1]["optional"] = True
	else:
		try:
			obtain_ticket(company, Service.PADRON_A5)
			steps.append(_step("padron", title, OK, _("'Complete from ARCA' is available."), optional=True))
		except ArcaError as exc:
			step = _failed("padron", title, exc, settings, service=WSAA_SERVICE_IDS[Service.PADRON_A5])
			step.update(status=WARNING, optional=True)
			steps.append(step)

	# 7. Purchase invoices (optional: until it works, they cannot be submitted)
	title = _("Verification of purchase invoices")
	if failed:
		skip("wscdc", title)
		steps[-1]["optional"] = True
	else:
		try:
			obtain_ticket(company, Service.WSCDC)
			steps.append(
				_step("wscdc", title, OK, _("Purchase invoices can be verified with ARCA."), optional=True)
			)
		except ArcaError as exc:
			step = _failed("wscdc", title, exc, settings, service=WSAA_SERVICE_IDS[Service.WSCDC])
			step.update(
				status=WARNING,
				optional=True,
				action=step["action"]
				+ " "
				+ _(
					"Until then, purchase invoices from suppliers that issue ARCA vouchers cannot be submitted."
				),
			)
			steps.append(step)

	return steps


@frappe.whitelist()
def verify_connection(company: str) -> dict:
	"""Run every check, store the result on ARCA Company Settings and return it."""
	frappe.get_doc("ARCA Company Settings", company).check_permission("write")
	steps = run_checks(company)
	ready = all(step["status"] == OK for step in steps if not step.get("optional"))
	status = READY if ready else NEEDS_ATTENTION

	settings = get_company_settings(company)
	settings.db_set(
		{
			"connection_status": status,
			"last_verified_on": now_datetime(),
			"connection_report": json.dumps(steps, ensure_ascii=False),
		},
		update_modified=False,
	)
	_complete_onboarding(steps, ready)
	return {"status": status, "steps": steps}


def _complete_onboarding(steps: list[dict], ready: bool) -> None:
	passed = {step["key"] for step in steps if step["status"] == OK}
	if ready:
		passed.add("all")
	for key, step in ONBOARDING_STEPS.items():
		if key in passed and frappe.db.exists("Onboarding Step", step):
			frappe.db.set_value("Onboarding Step", step, "is_complete", 1)


def notify_expiring_certificates() -> None:
	"""Daily job: warn System Managers 30, 15, 7, 3, 1 and 0 days before the active certificate expires, and weekly after."""
	now = now_datetime()
	for settings in frappe.get_all("ARCA Company Settings", fields=["name", "company", "environment"]):
		expiry = frappe.db.get_value(
			"ARCA Company Settings", settings.name, f"{settings.environment.lower()}_certificate_expiry"
		)
		if not expiry:
			continue
		days = days_until(get_datetime(expiry), now)
		if days in (30, 15, 7, 3, 1, 0) or (days < 0 and days % 7 == 0):
			_notify(settings.company, days)


def _notify(company: str, days: int) -> None:
	subject = (
		_("The ARCA certificate of {0} expires in {1} days.").format(company, days)
		if days > 0
		else _(
			"The ARCA certificate of {0} has expired. Invoices cannot be authorized until it is renewed."
		).format(company)
	)
	users = frappe.get_all(
		"Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent", distinct=True
	)
	for user in frappe.get_all(
		"User", filters={"name": ["in", users], "enabled": 1, "user_type": "System User"}, pluck="name"
	):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": "ARCA Company Settings",
				"document_name": company,
				"subject": subject,
			}
		).insert(ignore_permissions=True)
