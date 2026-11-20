from pathlib import Path

from babel.messages.pofile import read_po
from frappe.tests import UnitTestCase

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
REQUIRED_LOCALES = ("es",)


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
