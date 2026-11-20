"""Install, migrate and uninstall hooks."""

from arca_integration.arca.catalogs import seed_catalogs
from arca_integration.arca.custom_fields import create_arca_custom_fields, delete_arca_custom_fields


def after_install() -> None:
	seed_catalogs()
	create_arca_custom_fields()


def after_migrate() -> None:
	seed_catalogs()
	create_arca_custom_fields()


def before_uninstall() -> None:
	delete_arca_custom_fields()
