from pathlib import Path

import frappe
from babel.messages.pofile import read_po
from frappe.tests import UnitTestCase

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
REQUIRED_LOCALES = ("es",)
# Frappe merges every app's translations into one dictionary per language, so a
# string we translate differently from these apps would change their UI too.
SHARED_WITH_APPS = ("frappe", "erpnext")


def _read(path: Path):
	with path.open("rb") as f:
		return read_po(f)


class TestTranslations(UnitTestCase):
	"""Every source string in main.pot must have a final translation in each required locale."""

	def test_required_locales_are_complete(self):
		template = _read(LOCALE_DIR / "main.pot")

		for locale in REQUIRED_LOCALES:
			with self.subTest(locale=locale):
				catalog = _read(LOCALE_DIR / f"{locale}.po")
				missing = []
				for message in template:
					if not message.id:
						continue
					translated = catalog.get(message.id, context=message.context)
					if translated is None or not translated.string or translated.fuzzy:
						missing.append(message.id)

				self.assertEqual(missing, [], f"Untranslated strings in {locale}.po")

	def test_no_conflicts_with_other_apps(self):
		for locale in REQUIRED_LOCALES:
			ours = _read(LOCALE_DIR / f"{locale}.po")
			for app in SHARED_WITH_APPS:
				theirs_path = Path(frappe.get_app_path(app, "locale", f"{locale}.po"))
				if not theirs_path.exists():
					continue
				theirs = _read(theirs_path)
				conflicts = []
				for message in ours:
					other = theirs.get(message.id, context=message.context) if message.id else None
					if other and other.string and other.string != message.string:
						conflicts.append((message.id, other.string, message.string))

				with self.subTest(locale=locale, app=app):
					self.assertEqual(conflicts, [], f"Use {app}'s translation or rename the source string")
