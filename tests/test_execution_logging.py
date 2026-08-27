import logging
from pathlib import Path

from passagen.execution_logging import configure_execution_logging


def _reset_passagen_logger() -> None:
    logger = logging.getLogger("passagen")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def test_execution_log_uses_one_timestamped_file_and_latest_link(tmp_path: Path) -> None:
    _reset_passagen_logger()
    try:
        logs_dir = tmp_path / "logs"
        first_path = configure_execution_logging(debug=False, logs_dir=logs_dir)
        logging.getLogger("passagen.test").info("first execution")

        latest = logs_dir / "latest"
        assert first_path.suffix == ".txt"
        assert latest.samefile(first_path)
        assert "first execution" in latest.read_text(encoding="utf-8")
        assert list(logs_dir.glob("*.txt")) == [first_path]

        second_path = configure_execution_logging(debug=True, logs_dir=logs_dir)
        logging.getLogger("passagen.test").debug("second execution")

        assert second_path != first_path
        assert latest.samefile(second_path)
        assert "second execution" in latest.read_text(encoding="utf-8")
        assert sorted(logs_dir.glob("*.txt")) == sorted([first_path, second_path])
    finally:
        _reset_passagen_logger()
