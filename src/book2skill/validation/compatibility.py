"""Layered Agent Skills compatibility validation.

External validators run as optional, version-locked subprocesses.  They never
replace Book2Skill's provenance, security, copyright, or publication gates.
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill.domain import DEFAULT_TARGET_SPEC, TargetSpec
from book2skill.domain.contracts import SKILL_VALIDATOR_VERSION, SKILLS_REF_VERSION
from book2skill.storage.file_storage import atomic_write
from book2skill.validation.models import CheckStatus, QualityReport, ReportStatus

COMPATIBILITY_REPORT_FILENAME = "compatibility-report.json"
_MAX_OUTPUT_CHARS = 2000
_VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")


class ValidationProfile(StrEnum):
    """Strictness profile for portable Skill validation."""

    PORTABLE_DRAFT = "portable-draft"
    PORTABLE_RELEASE = "portable-release"


class VerificationLevel(StrEnum):
    """Strongest verification evidence recorded for one host."""

    NOT_VERIFIED = "not_verified"
    STRUCTURE_VALIDATED = "structure_validated"
    INSTALL_SMOKE_PASSED = "install_smoke_passed"
    RUNTIME_VERIFIED = "runtime_verified"


class ExternalValidationResult(BaseModel):
    """Sanitized result from one optional external validator."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str
    expected_version: str
    observed_version: str | None = None
    status: CheckStatus
    blocking: bool
    exit_code: int | None = None
    message: str
    evidence: list[str] = Field(default_factory=list)


class HostCompatibility(BaseModel):
    """Evidence level for one host adapter."""

    model_config = ConfigDict(extra="forbid")

    host: str
    level: VerificationLevel = VerificationLevel.NOT_VERIFIED
    evidence: list[str] = Field(default_factory=list)


class CompatibilityReport(BaseModel):
    """Machine-readable separation of structure, tool, and host evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    target_spec: TargetSpec
    profile: ValidationProfile
    status: ReportStatus
    internal_quality_status: ReportStatus
    external_results: list[ExternalValidationResult]
    hosts: list[HostCompatibility]


@dataclass(frozen=True)
class ExternalToolConfig:
    """Safe argv templates and version policy for one validator process."""

    tool_id: str
    expected_version: str
    command: tuple[str, ...]
    version_command: tuple[str, ...]
    distribution_name: str | None = None
    warning_exit_codes: tuple[int, ...] = ()
    blocking_in_release: bool = False


def default_external_tools() -> tuple[ExternalToolConfig, ...]:
    """Return pinned, offline-safe validator command definitions."""
    return (
        ExternalToolConfig(
            tool_id="agent-skills-reference",
            expected_version=SKILLS_REF_VERSION,
            # skills-ref==0.1.0 publishes the ``agentskills`` executable.
            # Keep the distribution name for version provenance, but invoke
            # the actual cross-platform entry point.
            command=("agentskills", "validate", "{skill_dir}"),
            version_command=("agentskills", "--version"),
            distribution_name="skills-ref",
            # The upstream README explicitly says this is demonstration-only.
            blocking_in_release=False,
        ),
        ExternalToolConfig(
            tool_id="skill-validator",
            expected_version=SKILL_VALIDATOR_VERSION,
            command=(
                "skill-validator",
                "check",
                "--strict",
                "--skip",
                "links",
                # Build outputs retain root-level audit sidecars (for
                # provenance, replay and quality evidence).  They are not
                # host-loaded Skill content, so ask the external validator to
                # treat that flat layout as intentional and skip orphan
                # warnings for those sidecars.  Book2Skill's own validator
                # and the reference validator still enforce the core
                # structure contract.
                "--skip-orphans",
                "--allow-flat-layouts",
                "--allow-dirs=wiki",
                "-o",
                "json",
                "{skill_dir}",
            ),
            version_command=("skill-validator", "--version"),
            warning_exit_codes=(2,),
            blocking_in_release=True,
        ),
    )


def default_host_compatibility() -> list[HostCompatibility]:
    """Return conservative structure-only evidence for supported adapters."""
    return [
        HostCompatibility(
            host=host,
            level=VerificationLevel.STRUCTURE_VALIDATED,
            evidence=["book2skill.internal_quality"],
        )
        for host in ("claude", "trae", "codex", "project", "chatgpt")
    ]


class ExternalValidatorRunner:
    """Run version-locked validator subprocesses without a shell."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        config: ExternalToolConfig,
        skill_dir: Path,
        *,
        profile: ValidationProfile,
    ) -> ExternalValidationResult:
        """Run one tool or return an explicit not-run result."""
        blocking = (
            profile == ValidationProfile.PORTABLE_RELEASE
            and config.blocking_in_release
        )
        executable = _resolve_executable(config.command[0])
        if executable is None:
            return ExternalValidationResult(
                tool_id=config.tool_id,
                expected_version=config.expected_version,
                status=CheckStatus.NOT_RUN,
                blocking=blocking,
                message=f"{config.command[0]} is not installed or not on PATH.",
                evidence=["external.tool_missing"],
            )

        observed = self._version(config, executable)
        version_mismatch = observed != config.expected_version
        argv = [
            executable if index == 0 else token.replace("{skill_dir}", str(skill_dir))
            for index, token in enumerate(config.command)
        ]
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return ExternalValidationResult(
                tool_id=config.tool_id,
                expected_version=config.expected_version,
                observed_version=observed,
                status=CheckStatus.FAIL if blocking else CheckStatus.NOT_RUN,
                blocking=blocking,
                message=(
                    f"External validation timed out after "
                    f"{self._timeout_seconds:g}s."
                ),
                evidence=["external.timeout"],
            )
        except OSError as exc:
            return ExternalValidationResult(
                tool_id=config.tool_id,
                expected_version=config.expected_version,
                observed_version=observed,
                status=CheckStatus.FAIL if blocking else CheckStatus.NOT_RUN,
                blocking=blocking,
                message=f"External validator could not start: {type(exc).__name__}.",
                evidence=["external.start_failed"],
            )

        status = _status_for_exit(completed.returncode, config.warning_exit_codes)
        evidence: list[str] = []
        if version_mismatch:
            evidence.append("external.version_mismatch")
            status = CheckStatus.FAIL if blocking else CheckStatus.WARN
        if completed.returncode != 0:
            evidence.append(f"external.exit_{completed.returncode}")
        message = _sanitized_message(completed, skill_dir)
        return ExternalValidationResult(
            tool_id=config.tool_id,
            expected_version=config.expected_version,
            observed_version=observed,
            status=status,
            blocking=blocking,
            exit_code=completed.returncode,
            message=message,
            evidence=evidence,
        )

    def _version(self, config: ExternalToolConfig, executable: str) -> str | None:
        if config.distribution_name:
            try:
                return importlib.metadata.version(config.distribution_name)
            except importlib.metadata.PackageNotFoundError:
                pass
        argv = [executable, *config.version_command[1:]]
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(self._timeout_seconds, 5.0),
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        match = _VERSION_RE.search(completed.stdout + "\n" + completed.stderr)
        return match.group(1) if match else None


def build_compatibility_report(
    skill_dir: Path,
    internal_report: QualityReport,
    *,
    profile: ValidationProfile = ValidationProfile.PORTABLE_DRAFT,
    run_external: bool = False,
    tools: tuple[ExternalToolConfig, ...] | None = None,
    runner: ExternalValidatorRunner | None = None,
    hosts: list[HostCompatibility] | None = None,
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC,
) -> CompatibilityReport:
    """Aggregate internal, external, and host evidence without conflating them."""
    configs = tools if tools is not None else default_external_tools()
    executor = runner or ExternalValidatorRunner()
    if run_external:
        external = [executor.run(tool, skill_dir, profile=profile) for tool in configs]
    else:
        external = [
            ExternalValidationResult(
                tool_id=tool.tool_id,
                expected_version=tool.expected_version,
                status=CheckStatus.NOT_RUN,
                blocking=(
                    profile == ValidationProfile.PORTABLE_RELEASE
                    and tool.blocking_in_release
                ),
                message="External validation was not requested.",
                evidence=["external.not_requested"],
            )
            for tool in configs
        ]
    status = _aggregate_compatibility(internal_report.status, external)
    return CompatibilityReport(
        target_spec=target_spec,
        profile=profile,
        status=status,
        internal_quality_status=internal_report.status,
        external_results=external,
        hosts=hosts if hosts is not None else default_host_compatibility(),
    )


def write_compatibility_report(
    skill_dir: Path, report: CompatibilityReport
) -> tuple[Path, Path]:
    """Atomically write JSON and Markdown compatibility reports."""
    root = Path(skill_dir)
    json_path = root / COMPATIBILITY_REPORT_FILENAME
    md_path = root / "compatibility-report.md"
    atomic_write(json_path, report.model_dump_json(indent=2))
    lines = [
        "# Compatibility Report",
        "",
        f"- Target: `{report.target_spec.name}@{report.target_spec.revision}`",
        f"- Profile: `{report.profile.value}`",
        f"- Status: `{report.status.value}`",
        "",
        "## External validators",
        "",
        "| Tool | Expected | Observed | Status | Blocking |",
        "|---|---|---|---|---|",
    ]
    for item in report.external_results:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.tool_id,
                    item.expected_version,
                    item.observed_version or "—",
                    item.status.value,
                    str(item.blocking).lower(),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Host evidence", ""])
    if report.hosts:
        for host in report.hosts:
            lines.append(f"- `{host.host}`: `{host.level.value}`")
    else:
        lines.append("- No host runtime evidence was recorded for this artifact.")
    lines.append("")
    atomic_write(md_path, "\n".join(lines))
    return md_path, json_path


def _resolve_executable(command: str) -> str | None:
    candidate = Path(command)
    if candidate.is_file():
        return str(candidate.resolve())
    return shutil.which(command)


def _status_for_exit(code: int, warnings: tuple[int, ...]) -> CheckStatus:
    if code == 0:
        return CheckStatus.PASS
    if code in warnings:
        return CheckStatus.WARN
    return CheckStatus.FAIL


def _sanitized_message(
    completed: subprocess.CompletedProcess[str], skill_dir: Path
) -> str:
    raw = (completed.stdout or completed.stderr or "No validator output.").strip()
    sanitized = raw.replace(str(skill_dir), "<skill_dir>")
    sanitized = sanitized.replace(str(skill_dir.resolve()), "<skill_dir>")
    return sanitized[:_MAX_OUTPUT_CHARS]


def _aggregate_compatibility(
    internal: ReportStatus, external: list[ExternalValidationResult]
) -> ReportStatus:
    if internal == ReportStatus.FAIL:
        return ReportStatus.FAIL
    if any(
        result.blocking and result.status in {CheckStatus.FAIL, CheckStatus.NOT_RUN}
        for result in external
    ):
        return ReportStatus.FAIL
    if internal == ReportStatus.PASS_WITH_WARNINGS or any(
        result.status != CheckStatus.PASS for result in external
    ):
        return ReportStatus.PASS_WITH_WARNINGS
    return ReportStatus.PASS


__all__ = [
    "COMPATIBILITY_REPORT_FILENAME",
    "CompatibilityReport",
    "ExternalToolConfig",
    "ExternalValidationResult",
    "ExternalValidatorRunner",
    "HostCompatibility",
    "ValidationProfile",
    "VerificationLevel",
    "build_compatibility_report",
    "default_external_tools",
    "default_host_compatibility",
    "write_compatibility_report",
]
