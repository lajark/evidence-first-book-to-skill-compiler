"""Shared CLI construction seams.

The command module remains the stable Typer facade.  Cross-command dependency
construction lives here so command handlers do not each know how profile files,
environment precedence, cache roots and routing strategies are wired.
"""

from __future__ import annotations

from pathlib import Path

import typer

from book2skill.config import Locale, load_env_file
from book2skill.llm.ports import LLMAdapter


def build_llm_adapter(
    kind: str | None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    allow_fallback: bool = False,
    data_home: Path | None = None,
    llm_profiles: str | None = None,
    llm_profile: str | None = None,
    llm_strategy: str = "single",
    locale: Locale = "zh-CN",
    env_file_path: str | Path | None = None,
) -> LLMAdapter:
    """Resolve and build the shared, auditable LLM adapter.

    Credentials are resolved only from profile env-var names or the process
    environment.  This seam deliberately accepts the already-resolved locale
    from the facade instead of importing CLI global state.
    """
    from book2skill.llm.profiles import (
        load_profile_set,
        profile_to_runtime_config,
    )
    from book2skill.llm.router import RouterLLMAdapter
    from book2skill.llm.runtime import (
        LLMRuntimeError,
        default_timing_history_path,
        resolve_runtime_config,
    )
    from book2skill.llm.runtime import build_llm_adapter as build_runtime_adapter

    env_file = load_env_file(env_file_path) if env_file_path else load_env_file()
    if llm_strategy == "balanced":
        if not llm_profiles:
            raise typer.BadParameter(
                "--llm-strategy balanced requires --llm-profiles <path>."
            )
        try:
            profile_set = load_profile_set(llm_profiles)
        except (LLMRuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        return RouterLLMAdapter(
            profile_set,
            allow_fallback=allow_fallback,
            locale=locale,
            data_home=data_home,
            env_file=env_file,
        )

    try:
        if llm_profiles:
            profile_set = load_profile_set(llm_profiles)
            try:
                profile = profile_set.profile(llm_profile)
            except KeyError as exc:
                raise typer.BadParameter(str(exc)) from exc
            config = profile_to_runtime_config(
                profile,
                allow_fallback=allow_fallback,
                locale=locale,
                env_file=env_file,
            )
        else:
            config = resolve_runtime_config(
                kind,
                model=model,
                base_url=base_url,
                allow_fallback=allow_fallback,
                locale=locale,
                env_file=env_file,
            )
    except (LLMRuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    cache_root = data_home / ".cache" / "llm" if data_home is not None else None
    timing_history_path = None
    if config.provider == "openai":
        timing_history_path = (
            data_home / ".cache" / "performance-history.json"
            if data_home is not None
            else default_timing_history_path()
        )
    return build_runtime_adapter(
        config,
        cache_root=cache_root,
        timing_history_path=timing_history_path,
    )


__all__ = ["build_llm_adapter"]
