import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_project_metadata_matches_the_frappe_app():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert data["project"]["name"] == "arca_integration"
    assert data["project"]["version"] == "0.1.0"
    assert data["tool"]["bench"]["frappe-dependencies"]["frappe"]


def test_module_folder_and_modules_txt_agree():
    modules = (ROOT / "arca_integration" / "modules.txt").read_text().strip()

    assert modules == "Arca Integration"
    assert (ROOT / "arca_integration" / "arca_integration").is_dir()


def test_the_app_package_carries_a_patches_file():
    """Frappe reads `<app>/patches.txt`, next to `hooks.py` and `modules.txt`.

    Observed on a Frappe v16 bench: without this file `bench build --app
    arca_integration` dies with `TypeError: paths[0] ... Received undefined`.
    Every Frappe app ships one; an empty file is valid.
    """
    package = ROOT / "arca_integration"

    assert (package / "patches.txt").is_file()
    assert (package / "hooks.py").is_file()
    assert (package / "modules.txt").is_file()


def test_the_license_declared_everywhere_agrees():
    """`hooks.py`, `LICENSE` and `pyproject.toml` must not drift apart."""
    hooks = (ROOT / "arca_integration" / "hooks.py").read_text()
    license_text = (ROOT / "LICENSE").read_text()
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert 'app_license = "mit"' in hooks
    assert license_text.startswith("MIT License")
    assert data["project"]["license"]["text"] == "MIT"
    assert "License :: OSI Approved :: MIT License" in data["project"]["classifiers"]


def test_every_declared_doctype_script_exists():
    """`doctype_js` is a silent no-op when the path is wrong: nothing fails.

    The forms would simply work less, so the mapping is checked here. `hooks.py`
    has no imports and is parsed as data.
    """
    import ast

    hooks_path = ROOT / "arca_integration" / "hooks.py"
    tree = ast.parse(hooks_path.read_text())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", None) == "doctype_js" for target in node.targets)
    )
    doctype_js = ast.literal_eval(assignment.value)

    assert doctype_js, "the invoice has to reach the comprobante somehow"

    for doctype, declared_scripts in doctype_js.items():
        scripts = (
            declared_scripts if isinstance(declared_scripts, list) else [declared_scripts]
        )
        for script in scripts:
            path = ROOT / "arca_integration" / script
            assert path.is_file(), f"{doctype} points at a missing script: {script}"
            assert path.suffix == ".js"


def test_the_comprobante_ships_its_form_and_list_scripts():
    """Frappe finds these by convention, so a rename breaks them silently."""
    folder = (
        ROOT
        / "arca_integration"
        / "arca_integration"
        / "doctype"
        / "arca_comprobante"
    )

    assert (folder / "arca_comprobante.js").is_file()
    assert (folder / "arca_comprobante_list.js").is_file()
    assert (folder / "__init__.py").is_file()

