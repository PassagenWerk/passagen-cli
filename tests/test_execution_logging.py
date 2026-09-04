import io
import logging
from pathlib import Path

from rich.console import Console

from passagen_cli.logging import (
    archive_execution_logs,
    configure_execution_logging,
    set_execution_log_level,
)


def _reset_passagen_logger() -> None:
    for logger_name in ("passagen", "passagen_cli"):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def test_execution_log_uses_one_timestamped_directory(tmp_path: Path) -> None:
    _reset_passagen_logger()
    try:
        logs_dir = tmp_path / "logs"
        first_dir = configure_execution_logging(debug=False, logs_dir=logs_dir)
        logging.getLogger("passagen.test").info("first execution")

        assert first_dir.parent == logs_dir
        assert "first execution" in (first_dir / "log.txt").read_text(encoding="utf-8")
        assert not (logs_dir / "latest").exists()

        second_dir = configure_execution_logging(debug=True, logs_dir=logs_dir)
        logging.getLogger("passagen.test").debug("second execution")

        assert second_dir != first_dir
        assert "second execution" in (second_dir / "log.txt").read_text(encoding="utf-8")
        assert sorted(path.name for path in logs_dir.iterdir()) == sorted(
            [first_dir.name, second_dir.name]
        )
    finally:
        _reset_passagen_logger()


def test_archive_execution_logs_moves_entries_except_excluded_run(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    first = logs_dir / "20260101-000000-000001"
    current = logs_dir / "20260102-000000-000001"
    first.mkdir(parents=True)
    current.mkdir()

    moved = archive_execution_logs(logs_dir, exclude=(current,))

    assert moved == [(first, logs_dir / "old" / first.name)]
    assert not first.exists()
    assert (logs_dir / "old" / first.name).is_dir()
    assert current.is_dir()


def test_terminal_handler_shows_only_warnings_by_default(tmp_path: Path) -> None:
    _reset_passagen_logger()
    try:
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, width=120)
        execution_dir = configure_execution_logging(
            debug=False,
            logs_dir=tmp_path / "logs",
            console=console,
        )
        logger = logging.getLogger("passagen.test")
        logger.info("info detail")
        logger.warning("provider degraded")

        output = buffer.getvalue()
        assert "provider degraded" in output
        assert "info detail" not in output
        file_log = (execution_dir / "log.txt").read_text(encoding="utf-8")
        assert "info detail" in file_log
    finally:
        _reset_passagen_logger()


def test_terminal_handler_follows_debug_level_changes(tmp_path: Path) -> None:
    _reset_passagen_logger()
    try:
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, width=120)
        configure_execution_logging(
            debug=False,
            logs_dir=tmp_path / "logs",
            console=console,
        )
        logger = logging.getLogger("passagen.test")

        set_execution_log_level(debug=True)
        logger.debug("debug detail")
        assert "debug detail" in buffer.getvalue()

        set_execution_log_level(debug=False)
        buffer.truncate(0)
        buffer.seek(0)
        logger.debug("hidden detail")
        logger.warning("still visible")
        output = buffer.getvalue()
        assert "hidden detail" not in output
        assert "still visible" in output
    finally:
        _reset_passagen_logger()
