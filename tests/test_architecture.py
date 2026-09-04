import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src"
CLI_ROOT = SOURCE_ROOT / "passagen_cli"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_cli_distribution_does_not_bundle_core_package() -> None:
    assert not (SOURCE_ROOT / "passagen").exists()


def test_cli_does_not_import_core_external_adapters() -> None:
    offenders = [
        path.relative_to(CLI_ROOT)
        for path in CLI_ROOT.rglob("*.py")
        if any(module.startswith("passagen.external") for module in _imports(path))
    ]
    assert offenders == []
