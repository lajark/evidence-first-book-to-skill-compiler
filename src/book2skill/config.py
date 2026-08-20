"""Lightweight ``.env`` loader — no third-party dependency.

Loads ``KEY=VALUE`` pairs from a ``.env`` file into a plain dict (it does
**not** mutate ``os.environ``). Real environment variables and shell exports
are consulted separately by the caller, so the lookup priority becomes:
CLI flag > system environment variable > ``.env`` file > built-in default.

The loader searches for ``.env`` starting from the current working directory
and walking up to the filesystem root (so commands run from a project
subdirectory still find the project-root ``.env``). A missing file is a
silent no-op — the pipeline stays fully offline-capable.

Format rules
------------
- One ``KEY=VALUE`` per line.
- Leading/trailing whitespace around key and value is stripped.
- Lines starting with ``#`` and blank lines are ignored.
- A value wrapped in matching single or double quotes keeps inner content
  verbatim (quotes are stripped). Inline ``#`` is *not* treated as a comment
  inside a value, to avoid surprising users whose secrets contain ``#``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

Locale = Literal["zh-CN", "en"]


class ConfigLoadError(ValueError):
    """A safe, user-facing configuration loading/validation failure."""


class CloudLLMConfig(BaseModel):
    """Optional remote LLM settings from ``config.example.yaml``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str | None = None
    model: str | None = None

    @field_validator("base_url", "model")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank when provided")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.enabled and (self.base_url is None or self.model is None):
            raise ValueError(
                "cloud_llm.base_url and cloud_llm.model are required when "
                "cloud_llm.enabled is true"
            )


class ConfigLimits(BaseModel):
    """Resource and source-quotation limits shared by pipeline commands."""

    model_config = ConfigDict(extra="forbid")

    max_input_mb: int = Field(default=200, ge=1)
    max_direct_quote_words: int = Field(default=25, ge=0)
    skill_target_tokens: int = Field(default=4000, ge=1)
    reference_target_tokens: int = Field(default=1500, ge=1)


class ExtensionConfig(BaseModel):
    """Extension registry policy from the versioned application config."""

    model_config = ConfigDict(extra="forbid")

    registry_dir: Path | None = None
    allow_unsigned: bool = False
    supported_manifest_versions: list[int] = Field(
        default_factory=lambda: [1], min_length=1
    )

    @field_validator("supported_manifest_versions")
    @classmethod
    def _validate_manifest_versions(cls, value: list[int]) -> list[int]:
        if any(version < 1 for version in value):
            raise ValueError(
                "supported_manifest_versions must contain positive integers"
            )
        if len(set(value)) != len(value):
            raise ValueError("supported_manifest_versions must not contain duplicates")
        return value


class AppConfig(BaseModel):
    """Strict contract for the repository's top-level YAML configuration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    language: Locale = "zh-CN"
    data_home: Path = Path("./workspace")
    default_mode: Literal["analyze", "build", "update"] = "analyze"
    network: Literal["disabled", "enabled"] = "disabled"
    cloud_llm: CloudLLMConfig = Field(default_factory=CloudLLMConfig)
    limits: ConfigLimits = Field(default_factory=ConfigLimits)
    extensions: ExtensionConfig = Field(default_factory=ExtensionConfig)
    hosts: list[str] = Field(default_factory=lambda: ["claude", "trae", "codex"])

    @field_validator("hosts")
    @classmethod
    def _validate_hosts(cls, value: list[str]) -> list[str]:
        supported = {"claude", "trae", "codex", "project", "chatgpt"}
        if not value:
            raise ValueError("hosts must contain at least one host")
        unknown = sorted(set(value) - supported)
        if unknown:
            raise ValueError(f"unsupported hosts: {', '.join(unknown)}")
        if len(set(value)) != len(value):
            raise ValueError("hosts must not contain duplicates")
        return value

    @model_validator(mode="after")
    def _validate_network_policy(self) -> AppConfig:
        if self.cloud_llm.enabled and self.network == "disabled":
            raise ValueError(
                "cloud_llm.enabled requires network: enabled; "
                "the default is offline"
            )
        return self


def _config_error(exc: ValidationError) -> ConfigLoadError:
    fields: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        fields.append(f"{location or '<root>'}: {error.get('msg', 'invalid value')}")
    return ConfigLoadError("Invalid configuration: " + "; ".join(fields))


def load_app_config(path: str | os.PathLike[str]) -> AppConfig:
    """Load and validate a top-level YAML config without reading credentials.

    Relative data and extension paths are resolved against the config file's
    directory, making the contract deterministic regardless of the caller's
    current working directory.  The loader is explicit; normal commands keep
    their existing CLI/environment precedence until a config is supplied.
    """

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigLoadError(f"Configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigLoadError(
            f"Could not read configuration file: {type(exc).__name__}"
        ) from exc
    if raw is None:
        raw = {}
    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise _config_error(exc) from exc

    base_dir = config_path.resolve().parent
    updates: dict[str, object] = {
        "data_home": _resolve_config_path(config.data_home, base_dir),
    }
    extension_root = config.extensions.registry_dir
    if extension_root is not None:
        updates["extensions"] = config.extensions.model_copy(
            update={"registry_dir": _resolve_config_path(extension_root, base_dir)}
        )
    return config.model_copy(update=updates)


def _resolve_config_path(path: Path, base_dir: Path) -> Path:
    return path if path.is_absolute() else (base_dir / path).resolve()

__all__ = [
    "AppConfig",
    "CloudLLMConfig",
    "ConfigLimits",
    "ConfigLoadError",
    "ExtensionConfig",
    "Locale",
    "load_app_config",
    "load_env_file",
    "resolve_locale",
]


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse a single ``.env`` line into a ``(key, value)`` pair.

    Returns ``None`` for blank lines, comments, and lines without ``=``.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    # Strip one matching pair of surrounding quotes (single or double).
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def _find_env_file(start: Path) -> Path | None:
    """Walk up from *start* to the root, returning the first ``.env`` found."""
    start = start.resolve()
    for candidate in [start, *start.parents]:
        env_path = candidate / ".env"
        if env_path.is_file():
            return env_path
    return None


def load_env_file(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Load ``.env`` into a dict without touching ``os.environ``.

    Args:
        path: Explicit ``.env`` path. When omitted, searches from the
            current working directory upward.

    Returns:
        A mapping of the parsed keys to their values. Empty when no file is
        found. Later duplicate keys in the file overwrite earlier ones.
    """
    if path is not None:
        env_path = Path(path)
        if not env_path.is_file():
            return {}
    else:
        found = _find_env_file(Path.cwd())
        if found is None:
            return {}
        env_path = found

    loaded: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(line)
        if parsed is not None:
            loaded[parsed[0]] = parsed[1]
    return loaded


def resolve_locale(
    locale: str | None = None,
    *,
    environment: dict[str, str] | None = None,
    env_file: dict[str, str] | None = None,
    configured_locale: Locale | None = None,
) -> Locale:
    """Resolve the UI locale with CLI > environment > ``.env`` priority.

    Only the stable ``zh-CN`` and ``en`` tags are emitted into audit data.
    Common shell aliases are accepted as input to avoid platform-specific
    locale spellings leaking into generated artifacts.  An explicit config
    file may provide a lower-priority default through ``configured_locale``.
    """
    shell = environment if environment is not None else dict(os.environ)
    value = (
        locale
        or shell.get("BOOK2SKILL_LOCALE")
        or (env_file or {}).get("BOOK2SKILL_LOCALE")
        or configured_locale
    )
    normalized = (value or "zh-CN").replace("_", "-").lower()
    aliases: dict[str, Locale] = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("BOOK2SKILL_LOCALE must be one of: zh-CN, en") from exc
