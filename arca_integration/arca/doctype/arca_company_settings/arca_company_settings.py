# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import convert_utc_to_system_timezone, now_datetime

from arca_integration.services.credentials import (
	Credentials,
	InvalidCredential,
	build_csr,
	generate_private_key,
	is_encrypted_key,
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


MAX_PEM_LENGTH = 20_000


@frappe.whitelist()
def load_credentials(
	name: str,
	environment: str,
	certificate: str | None = None,
	private_key: str | None = None,
	save: bool = False,
) -> dict:
	"""Check, and with `save` store, a certificate and/or private key read from files in the browser.

	The browser sends the files' text; nothing is stored as a File. The key is
	saved encrypted. Without `save` it only returns what the dialog shows.
	"""
	doc = frappe.get_doc("ARCA Company Settings", name)
	doc.check_permission("write")
	environment = Environment(environment)
	prefix = _prefix(environment)

	if not certificate and not private_key:
		frappe.throw(_("Choose the certificate file, the private key file or both."))
	for text in (certificate, private_key):
		if text and len(text) > MAX_PEM_LENGTH:
			frappe.throw(_("The file is too large to be a certificate or a key."))

	if private_key:
		if is_encrypted_key(private_key):
			frappe.throw(
				_(
					"The private key is protected with a password. Remove it and load the new file: openssl pkey -in key.key -out key_without_password.key"
				),
				title=_("Private Key"),
			)
		try:
			private_key = validate_private_key(private_key)
		except InvalidCredential:
			frappe.throw(
				_("The private key file is not a PEM private key ('-----BEGIN PRIVATE KEY-----')."),
				title=_("Private Key"),
			)

	info = None
	if certificate:
		try:
			certificate = normalize_pem(certificate)
			info = read_certificate(certificate)
		except InvalidCredential:
			frappe.throw(
				_("The certificate file is not a PEM certificate ('-----BEGIN CERTIFICATE-----')."),
				title=_("Certificate"),
			)

	stored_certificate = doc.get(f"{prefix}_certificate")
	stored_key = doc.get_private_key(environment)
	final_certificate = certificate or stored_certificate
	final_key = private_key or stored_key

	matches = None
	if final_certificate and final_key:
		matches = key_matches_certificate(final_key, final_certificate)
		if not matches:
			frappe.throw(
				_("The certificate does not match the private key. Load both files from the same request.")
				if certificate and private_key
				else _(
					"The certificate does not match the private key already stored. Load the certificate issued for the CSR generated here, or load both files together."
				),
				title=_("Certificate"),
			)

	warnings = []
	summary = {"key_loaded": bool(final_key), "matches": matches, "warnings": warnings}
	if info:
		days_left = (info.not_after.date() - now_datetime().date()).days
		if days_left < 0:
			frappe.throw(
				_("The certificate expired on {0}.").format(frappe.format(info.not_after.date(), "Date"))
			)
		if days_left <= 30:
			warnings.append(_("The certificate expires in {0} days.").format(days_left))
		if info.cuit and info.cuit != doc.cuit:
			warnings.append(
				_(
					"The certificate belongs to CUIT {0} and the company is {1}. It only works if CUIT {0} was delegated the service."
				).format(info.cuit, doc.cuit)
			)
		summary.update(
			subject=info.subject,
			cuit=info.cuit,
			expires_on=frappe.format(info.not_after.date(), "Date"),
			days_left=days_left,
		)

	if frappe.parse_json(save):
		if certificate:
			doc.set(f"{prefix}_certificate", certificate)
		if private_key:
			doc.set(f"{prefix}_private_key", private_key)
		doc.save()
		summary["saved"] = True
	return summary
