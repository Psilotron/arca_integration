# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from arca_integration.services.endpoints import Environment, Service, resolve_endpoint


class ARCASettings(Document):
	def validate(self):
		seen = set()
		for row in self.endpoint_overrides:
			key = (row.service, row.environment)
			if key in seen:
				frappe.throw(
					_("Row {0}: there is already an override for {1} in {2}.").format(
						row.idx, row.service, _(row.environment)
					)
				)
			seen.add(key)

			if not (row.url or "").startswith("https://"):
				frappe.throw(_("Row {0}: the URL must start with https://.").format(row.idx))


def get_endpoint(service: Service | str, environment: Environment | str) -> str:
	"""URL for `service` in `environment`, applying the overrides from ARCA Settings."""
	settings = frappe.get_cached_doc("ARCA Settings")
	overrides = {(row.service, row.environment): row.url for row in settings.endpoint_overrides}
	return resolve_endpoint(service, environment, overrides)


def get_request_timeout() -> int:
	return frappe.get_cached_doc("ARCA Settings").request_timeout or 30
