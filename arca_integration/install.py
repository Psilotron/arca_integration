"""Install, migrate and uninstall hooks."""

from arca_integration.arca.catalogs import link_standard_records, seed_catalogs
from arca_integration.arca.custom_fields import create_arca_custom_fields, delete_arca_custom_fields


def after_install() -> None:
	seed_catalogs()
	create_arca_custom_fields()


def after_migrate() -> None:
	seed_catalogs()
	create_arca_custom_fields()
	link_standard_records()


def before_uninstall() -> None:
	delete_arca_custom_fields()
