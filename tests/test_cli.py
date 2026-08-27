from pathlib import Path

import pytest
from typer.testing import CliRunner

from passagen.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Manage paper PDFs" in result.stdout


def test_database_init_uses_current_directory_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "data" / "passagen.db").exists()


def test_database_init_and_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "new" / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"])
    assert result.exit_code == 0
    assert (data_dir / "passagen.db").exists()

    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "status"])
    assert result.exit_code == 0
    assert "schema version: 1" in result.stdout
