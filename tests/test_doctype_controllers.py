"""Frappe finds a controller by the doctype name, not by the file name.

`frappe.model.document.import_controller` computes

    classname = doctype.replace(" ", "").replace("-", "")

and raises ``ImportError`` when the module has no such class. A file whose class
is named anything else looks fine in review and fails at the first ``get_doc``.
"""

import json
import re
from pathlib import Path

ROOT = (
    Path(__file__).parents[1] / "arca_integration" / "arca_integration" / "doctype"
)


def _documents():
    for path in sorted(ROOT.glob("*/*.json")):
        yield path, json.loads(path.read_text())


def test_every_controller_class_is_named_the_way_frappe_looks_it_up():
    for path, doc in _documents():
        source = path.with_suffix(".py").read_text()
        found = re.search(r"^class (\w+)\(Document\)", source, re.MULTILINE)

        assert found, f"{path} has no Document subclass"
        expected = doc["name"].replace(" ", "").replace("-", "")
        assert found.group(1) == expected, path
