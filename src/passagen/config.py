from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("passagen.yaml")
ENV_PREFIX = "PASSAGEN_"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="forbid")

    data_dir: Path = Path("data")
    database_path: Path | None = None
    debug: bool = False

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir.expanduser().resolve()

    @property
    def resolved_database_path(self) -> Path:
        if self.database_path:
            return self.database_path.expanduser().resolve()
        return self.resolved_data_dir / "passagen.db"


class ConfigError(ValueError):
    pass


def load_settings(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    values = _read_config(path) if path.exists() else {}

    # BaseSettings gives constructor values priority over environment variables.
    # Remove file values that have an environment override before validation.
    for field_name in Settings.model_fields:
        if f"{ENV_PREFIX}{field_name}".upper() in os.environ:
            values.pop(field_name, None)
    values.update({key: value for key, value in (overrides or {}).items() if value is not None})

    try:
        return Settings(**values)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def _read_config(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as config_file:
            document = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}") from exc

    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ConfigError(f"Config {path} must contain a mapping")

    values = document.get("passagen", document)
    if not isinstance(values, dict):
        raise ConfigError(f"Config {path} section 'passagen' must contain a mapping")
    return values
