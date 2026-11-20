# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import convert_utc_to_system_timezone

from arca_integration.services.credentials import (
	Credentials,
	InvalidCredential,
	build_csr,
	generate_private_key,
	is_valid_cuit,
	key_matches_certificate,
	normalize_cuit,
	normalize_pem,
	read_certificate,
	validate_private_key,
)
from arca_integration.services.endpoints import Environment

_COMMON_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


def _prefix(environment: Environment | str) -> str:
	return Environment(environment).value.lower()


class ARCACompanySettings(Document):
	def validate(self):
		self.cuit = normalize_cuit(self.cuit)
		if not is_valid_cuit(self.cuit):
			frappe.throw(_("CUIT {0} is not valid.").format(self.cuit))

		for environment in Environment:
			self._validate_credentials(environment)

	def _validate_credentials(self, environment: Environment):
		prefix = _prefix(environment)
		certificate = self.get(f"{prefix}_certificate")
		key_field = f"{prefix}_private_key"

		new_key = self.get(key_field)
		if new_key and not self.is_dummy_password(new_key):
			try:
				self.set(key_field, validate_private_key(new_key))
			except InvalidCredential:
				frappe.throw(
					_("The {0} private key is not a valid unencrypted PEM key.").format(_(environment.value))
				)

		if not certificate:
			self.set(f"{prefix}_certificate_subject", None)
			self.set(f"{prefix}_certificate_expiry", None)
			return

		try:
			certificate = normalize_pem(certificate)
			info = read_certificate(certificate)
		except InvalidCredential:
			frappe.throw(
				_("The {0} certificate is not a valid PEM certificate.").format(_(environment.value))
			)

		self.set(f"{prefix}_certificate", certificate)
		self.set(f"{prefix}_certificate_subject", info.subject)
		self.set(
			f"{prefix}_certificate_expiry",
			convert_utc_to_system_timezone(info.not_after).replace(tzinfo=None),
		)

		private_key = self.get_private_key(environment)
		if private_key and not key_matches_certificate(private_key, certificate):
			frappe.throw(
				_("The {0} certificate was not issued for the stored private key.").format(
					_(environment.value)
				)
			)

	def get_private_key(self, environment: Environment | str) -> str | None:
		return self.get_password(f"{_prefix(environment)}_private_key", raise_exception=False)

	def get_credentials(self, environment: Environment | str | None = None) -> Credentials:
		"""Credentials for `environment` (the active one by default). Server-side use only."""
		environment = Environment(environment or self.environment)
		certificate = self.get(f"{_prefix(environment)}_certificate")
		private_key = self.get_private_key(environment)
		if not certificate or not private_key:
			frappe.throw(
				_("Company {0} has no certificate and private key for {1}.").format(
					self.company, _(environment.value)
				)
			)
		return Credentials(cuit=self.cuit, certificate=certificate, private_key=private_key)


def get_company_settings(company: str) -> ARCACompanySettings:
	if not frappe.db.exists("ARCA Company Settings", company):
		frappe.throw(_("Set up ARCA Company Settings for {0}.").format(company))
	return frappe.get_doc("ARCA Company Settings", company)


@frappe.whitelist()
def generate_csr(name: str, environment: str, common_name: str) -> dict:
	"""Create a new private key for `environment` and return only its CSR.

	The key is stored encrypted and the old certificate is cleared, since it no
	longer matches the key.
	"""
	doc = frappe.get_doc("ARCA Company Settings", name)
	doc.check_permission("write")

	if not _COMMON_NAME_RE.match(common_name or ""):
		frappe.throw(_("The certificate name may only contain letters, numbers, hyphens and underscores."))

	environment = Environment(environment)
	prefix = _prefix(environment)
	private_key = generate_private_key()
	csr = build_csr(private_key, doc.cuit, doc.company, common_name)

	doc.set(f"{prefix}_private_key", private_key)
	doc.set(f"{prefix}_certificate", None)
	doc.save()

	return {"filename": f"{doc.cuit}-{common_name}.csr", "csr": csr}
