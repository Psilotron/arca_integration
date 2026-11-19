import json
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _items(path):
    data = json.loads(path.read_text())
    return [
        (item["link_type"], item["link_to"], item["label"])
        for item in data["items"]
    ]


def test_sidebar_formats_stay_in_sync():
    legacy = _items(ROOT / "arca_integration" / "workspace_sidebar" / "Arca Integration.json")
    current_path = (
        ROOT
        / "arca_integration"
        / "arca_integration"
        / "sidebar"
        / "arca_integration"
        / "arca_integration.json"
    )
    current = _items(current_path)

    assert current == legacy
    assert len(current) == 6
    assert all(not label.startswith("ARCA ") for _, _, label in current)
    assert current[0] == ("DocType", "ARCA Settings", "Configuracion ARCA")

    legacy_data = json.loads(
        (ROOT / "arca_integration" / "workspace_sidebar" / "Arca Integration.json").read_text()
    )
    assert all(item["icon"] for item in legacy_data["items"])
    current_data = json.loads(current_path.read_text())
    assert all(item["icon"] for item in current_data["items"])
    assert all(item["is_default_module"] == 1 for item in current_data["items"])
