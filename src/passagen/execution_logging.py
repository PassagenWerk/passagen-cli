import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

_LOGGER_NAME = "passagen"


def configure_execution_logging(*, debug: bool, logs_dir: Path = Path("logs")) -> Path:
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    log_path = logs_dir / f"{timestamp}.txt"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "[%(levelname)s] %(asctime)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    latest = logs_dir / "latest"
    if latest.is_dir() and not latest.is_symlink():
        shutil.rmtree(latest)
    else:
        latest.unlink(missing_ok=True)
    try:
        latest.symlink_to(log_path.name)
    except OSError:
        os.link(log_path, latest)
    return log_path


def set_execution_log_level(*, debug: bool) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)
