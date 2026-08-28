import logging
from pathlib import Path

from passagen.cli.logging import archive_execution_logs, configure_execution_logging


def _reset_passagen_logger() -> None:
    logger = logging.getLogger("passagen")
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
