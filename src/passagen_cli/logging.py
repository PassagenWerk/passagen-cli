import logging
import shutil
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_LOGGER_NAMES = ("passagen", "passagen_cli")


def configure_execution_logging(
    *,
    debug: bool,
    logs_dir: Path = Path("logs"),
    console: Console | None = None,
) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    execution_dir = logs_dir / timestamp
    execution_dir.mkdir(parents=True)
    for logger_name in _LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        for existing in logger.handlers[:]:
            logger.removeHandler(existing)
            existing.close()
        handler = logging.FileHandler(execution_dir / "log.txt", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "[%(levelname)s] %(asctime)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler.setLevel(logging.DEBUG if debug else logging.INFO)
        logger.addHandler(handler)
        if console is not None:
            terminal = RichHandler(
                console=console,
                show_time=False,
                show_path=debug,
                markup=False,
                rich_tracebacks=True,
            )
            terminal.setLevel(_terminal_level(debug=debug))
            logger.addHandler(terminal)
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        logger.propagate = False
    return execution_dir


def _terminal_level(*, debug: bool) -> int:
    return logging.DEBUG if debug else logging.WARNING


def archive_execution_logs(
    logs_dir: Path = Path("logs"),
    *,
    exclude: Iterable[Path] = (),
) -> list[tuple[Path, Path]]:
    if not logs_dir.is_dir():
        return []
    archive_dir = logs_dir / "old"
    excluded = {path.resolve() for path in exclude}
    moved: list[tuple[Path, Path]] = []
    for entry in sorted(logs_dir.iterdir()):
        if entry.name == "old" or entry.resolve() in excluded:
            continue
        archive_dir.mkdir(exist_ok=True)
        target = archive_dir / entry.name
        if target.exists():
            raise FileExistsError(f"Archived log already exists: {target}")
        shutil.move(str(entry), target)
        moved.append((entry, target))
    return moved


def set_execution_log_level(*, debug: bool) -> None:
    for logger_name in _LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        for handler in logger.handlers:
            if isinstance(handler, RichHandler):
                handler.setLevel(_terminal_level(debug=debug))
            else:
                handler.setLevel(logging.DEBUG if debug else logging.INFO)
