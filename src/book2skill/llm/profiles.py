"""Versioned provider profile contracts for multi-channel LLM routing.

A :class:`ProviderProfile` declares one OpenAI-compatible channel: endpoint,
model, the *name* of the environment variable holding its credential (never
the credential itself), the roles it may serve, scheduling/cost limits and a
data-sending policy. Profiles are consumed by the application layer and, in
later milestones, by the ``balanced``/``quality`` routers (OPT-P1-09/10).

Security contract
------------------
* Credentials are resolved at call time from the named environment variable
  (:func:`resolve_credentials`) and never written into config files,
  manifests, caches or logs.
* The profile model is ``extra="forbid"``: an accidental ``api_key: sk-...``
  literal in a profile file is rejected before any network call.
* ``api_key_env`` must be a valid environment-variable *name*; a literal
  credential value placed there is rejected.
* A profile may refuse a material category
  (:meth:`ProviderProfile.accepts_material`); routing to an incompatible
  profile for an unauthorized material is the caller's fail-closed
  responsibility (exercised by the router in OPT-P1-09).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from book2skill.config import Locale
from book2skill.llm.runtime import (
    LLMRuntimeConfig,
    LLMUnavailableError,
    Provider,
)

Role = Literal[
    "map",
    "section_reduce",
    "book_reduce",
    "synthesis",
    "skill",
    "critic",
    "arbiter",
]
DataSendPolicy = Literal[
    "original_text_allowed",
    "evidence_cards_only",
    "no_send",
]
MaterialCategory = Literal[
    "licensed_book",
    "internal_doc",
    "public_doc",
    "user_provided",
]

_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ProviderProfile(BaseModel):
    """One declared LLM channel with redacted credential handling."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(..., min_length=1)
    provider: Provider = "openai"
    model: str = Field(..., min_length=1)
    base_url: str | None = None
    api_key_env: str = Field(..., min_length=1)
    roles: list[Role] = Field(..., min_length=1)
    max_concurrent_requests: int = Field(default=2, ge=1)
    requests_per_minute: int = Field(default=15, ge=1)
    tokens_per_minute: int | None = Field(default=None, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    streaming: bool = False
    max_retries: int = Field(default=2, ge=0)
    circuit_failure_threshold: int = Field(default=3, ge=1)
    cost_per_million_input_tokens: float | None = Field(default=None, ge=0)
    cost_per_million_output_tokens: float | None = Field(default=None, ge=0)
    data_send_policy: DataSendPolicy = "original_text_allowed"
    allowed_material_categories: list[MaterialCategory] | None = None
    locale: Locale | None = None

    @field_validator("profile_id")
    @classmethod
    def _validate_profile_id(cls, value: str) -> str:
        if not _PROFILE_ID_RE.match(value):
            raise ValueError(
                "profile_id must be lowercase kebab-case (letters, digits, "
                "hyphens), e.g. 'bailian-flash'."
            )
        return value

    @field_validator("api_key_env")
    @classmethod
    def _validate_env_name(cls, value: str) -> str:
        if not _ENV_VAR_RE.match(value):
            raise ValueError(
                "api_key_env must be the NAME of an environment variable "
                "(uppercase letters, digits, underscores), not a credential."
            )
        return value

    def accepts_material(self, category: MaterialCategory) -> bool:
        """Whether this profile may receive *category* material at all."""
        if self.data_send_policy == "no_send":
            return False
        if self.allowed_material_categories is None:
            return True
        return category in self.allowed_material_categories

    def can_serve(self, role: Role) -> bool:
        """Whether this profile is permitted to serve *role*."""
        return role in self.roles


class ProviderProfileSet(BaseModel):
    """A versioned collection of provider profiles."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profiles: list[ProviderProfile] = Field(..., min_length=1)
    default_profile: str

    @model_validator(mode="after")
    def _validate_set(self) -> ProviderProfileSet:
        ids = [p.profile_id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("profile_id values must be unique")
        if self.default_profile not in ids:
            raise ValueError(
                f"default_profile '{self.default_profile}' is not declared "
                "in profiles"
            )
        return self

    def profile(self, profile_id: str | None) -> ProviderProfile:
        """Return the named profile, or the default when *profile_id* is None."""
        target = profile_id or self.default_profile
        for candidate in self.profiles:
            if candidate.profile_id == target:
                return candidate
        raise KeyError(f"Unknown provider profile: {target}")

    def active(
        self, *, env_file: dict[str, str] | None = None
    ) -> ProviderProfileSet:
        """Return a new set containing only profiles whose credential resolves.

        Dynamic channel activation: a template may reserve N channel slots and
        only the slots whose ``api_key_env`` has a credential (process
        environment first, then *env_file*) take part in routing. Mock
        profiles are always active (they need no credential).

        The default profile must remain active; if its credential is missing
        this raises :class:`ValueError` (fail-closed) so a dynamic channel
        count never silently drops the default channel.
        """
        def _has_credential(profile: ProviderProfile) -> bool:
            if profile.provider == "mock":
                return True
            return bool(
                os.environ.get(profile.api_key_env)
                or (env_file or {}).get(profile.api_key_env)
            )

        active_profiles = [p for p in self.profiles if _has_credential(p)]
        active_ids = {p.profile_id for p in active_profiles}
        if self.default_profile not in active_ids:
            raise ValueError(
                f"default_profile '{self.default_profile}' has no credential; "
                "cannot build a dynamic profile set without the default channel"
            )
        return ProviderProfileSet(
            profiles=active_profiles, default_profile=self.default_profile
        )


def resolve_credentials(
    profile: ProviderProfile,
    *,
    env_file: dict[str, str] | None = None,
) -> str:
    """Read the credential for *profile* from its named env var at call time.

    The value is returned to the caller and never persisted into config,
    manifests, caches or logs. The process environment takes precedence; when
    empty, *env_file* (a ``.env``-loaded dict) is consulted as a fallback so
    multi-profile keys can live in ``.env`` like single-provider config. A
    missing credential raises
    :class:`~book2skill.llm.runtime.LLMUnavailableError` (fail-closed).
    """
    value = os.environ.get(profile.api_key_env)
    if not value and env_file:
        value = env_file.get(profile.api_key_env)
    if not value:
        raise LLMUnavailableError(
            f"Provider profile '{profile.profile_id}' requires credential env "
            f"var '{profile.api_key_env}'. Set it in the environment or .env; "
            "never write the credential value into the profile file."
        )
    return value


def profile_to_runtime_config(
    profile: ProviderProfile,
    *,
    allow_fallback: bool = False,
    locale: Locale = "zh-CN",
    env_file: dict[str, str] | None = None,
) -> LLMRuntimeConfig:
    """Map a profile to the resolved runtime config, injecting live credentials.

    Mock profiles short-circuit to the deterministic offline runtime. Real
    profiles resolve their credential via :func:`resolve_credentials` so the
    secret stays in-process and is not stored on the returned config beyond
    the runtime's existing redacted handling. *env_file* supplies a ``.env``
    fallback for credentials not present in the process environment.
    """
    if profile.provider == "mock":
        return LLMRuntimeConfig(
            provider="mock",
            locale=profile.locale or locale,
            profile_id=profile.profile_id,
            data_send_policy=profile.data_send_policy,
            tokens_per_minute=profile.tokens_per_minute,
        )
    api_key = resolve_credentials(profile, env_file=env_file)
    return LLMRuntimeConfig(
        provider=profile.provider,
        model=profile.model,
        base_url=profile.base_url,
        api_key=api_key,
        allow_fallback=allow_fallback,
        locale=profile.locale or locale,
        max_retries=profile.max_retries,
        circuit_failure_threshold=profile.circuit_failure_threshold,
        max_concurrent_requests=profile.max_concurrent_requests,
        requests_per_minute=profile.requests_per_minute,
        tokens_per_minute=profile.tokens_per_minute,
        request_timeout_seconds=profile.request_timeout_seconds,
        streaming=profile.streaming,
        profile_id=profile.profile_id,
        data_send_policy=profile.data_send_policy,
    )


def load_profile_set(path: str | os.PathLike[str] | Path) -> ProviderProfileSet:
    """Load and strictly validate a profile set from a YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"LLM profile file must be a mapping, got {type(raw).__name__}"
        )
    return ProviderProfileSet.model_validate(raw)


def profile_set_from_env(
    env_file: dict[str, str] | None = None,
    *,
    environment: dict[str, str] | None = None,
) -> ProviderProfileSet | None:
    """Synthesize a single-profile set from the legacy single-provider env.

    Preserves backward compatibility for users who configured one provider
    via ``.env``/``BOOK2SKILL_LLM``. Returns ``None`` when no real provider
    is configured (so the caller falls back to the default Mock).
    """
    shell = environment if environment is not None else dict(os.environ)
    file_values = env_file or {}

    def pick(keys: tuple[str, ...]) -> str | None:
        for key in keys:
            if shell.get(key):
                return shell[key]
        for key in keys:
            if file_values.get(key):
                return file_values[key]
        return None

    kind = (pick(("BOOK2SKILL_LLM",)) or "mock").lower()
    if kind != "openai" and kind != "compatible":
        return None
    model = pick(("LLM_MODEL", "OPENAI_MODEL")) or "gpt-4o"
    base_url = pick(("LLM_BASE_URL", "OPENAI_BASE_URL"))
    api_key_env = "LLM_API_KEY" if (
        shell.get("LLM_API_KEY") or file_values.get("LLM_API_KEY")
    ) else "OPENAI_API_KEY"
    return ProviderProfileSet(
        profiles=[
            ProviderProfile(
                profile_id="default",
                provider="openai",
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                roles=["map", "section_reduce", "book_reduce", "synthesis", "skill"],
            )
        ],
        default_profile="default",
    )


__all__ = [
    "DataSendPolicy",
    "MaterialCategory",
    "ProviderProfile",
    "ProviderProfileSet",
    "Role",
    "load_profile_set",
    "profile_set_from_env",
    "profile_to_runtime_config",
    "resolve_credentials",
]
