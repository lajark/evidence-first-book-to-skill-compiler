"""Application-facing service used by the optional Windows WebGUI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from book2skill import __version__
from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.build import BuildUseCase
from book2skill.application.models import AnalysisBundle
from book2skill.application.progress import ProgressReporter
from book2skill.cli_support import build_llm_adapter
from book2skill.compiler import SkillSpec
from book2skill.desktop.jobs import (
    CancellationProgressReporter,
    JobBusyError,
    JobContext,
    JobManager,
)
from book2skill.desktop.runtime_paths import application_data_root
from book2skill.domain.errors import DomainError
from book2skill.hosts import get_installer
from book2skill.storage import atomic_write


class DesktopRequestError(ValueError):
    """Raised when a WebGUI request is invalid before pipeline execution."""

    code = "DESKTOP_REQUEST_INVALID"


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

_DEFAULT_REQUIRED_INPUTS = [
    "A legally held source document or a verified analysis bundle."
]
_DEFAULT_OUTPUTS = [
    "A concise, source-traceable response or action plan; never raw book text."
]


class DesktopApplicationService:
    """Translate JSON requests into existing application use cases.

    This class deliberately contains no extraction, compilation or host
    business rules.  It only supplies desktop defaults, converts request
    fields to typed application inputs and serialises small result summaries.
    """

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        manager: JobManager | None = None,
    ) -> None:
        self.root = (data_root or application_data_root()).resolve()
        self.data_home = self.root / "workspace"
        self.output_root = self.root / "output"
        self.bundle_dir = self.output_root / "bundles"
        self.skill_dir = self.output_root / "skills"
        self.manager = manager or JobManager()

    def bootstrap(self) -> dict[str, object]:
        """Return non-sensitive capabilities and current job state."""

        return {
            "product": "book2skill-desktop",
            "version": __version__,
            "platform": "windows",
            "offline_default": True,
            "formats": ["pdf", "epub", "docx", "html", "rtf", "txt", "md"],
            "hosts": ["claude", "trae", "codex", "project", "chatgpt"],
            "paths": {
                "root": str(self.root),
                "bundles": str(self.bundle_dir),
                "skills": str(self.skill_dir),
            },
            "job": self.manager.status(),
        }

    def start_job(self, kind: str, payload: dict[str, object]) -> str:
        """Start an ``analyze`` or ``build`` task in the background."""

        if kind not in {"analyze", "build"}:
            raise DesktopRequestError(f"Unsupported desktop job: {kind}")

        try:
            return self.manager.start(
                kind,
                lambda context: self._run(kind, payload, context),
            )
        except JobBusyError:
            raise

    def cancel_job(self) -> bool:
        return self.manager.cancel()

    def install(self, payload: dict[str, object]) -> dict[str, object]:
        """Install a generated Skill using the existing host installer."""

        skill_dir = self._required_path(payload, "skill_dir", must_exist=True)
        host = str(payload.get("host") or "").strip().lower()
        if not host:
            raise DesktopRequestError("host is required")
        try:
            installer = get_installer(
                host,
                project_level=bool(payload.get("project_level", False)),
                project_root=self._optional_path(payload, "project_root"),
                backup_root=self._optional_path(payload, "backup_root"),
                target_dir=(
                    str(payload["target_dir"])
                    if payload.get("target_dir")
                    else None
                ),
            )
            record = installer.install(
                skill_dir,
                dry_run=bool(payload.get("dry_run", True)),
                no_backup=bool(payload.get("no_backup", False)),
            )
        except (DomainError, ValueError) as exc:
            raise DesktopRequestError(str(exc)) from exc
        return {
            "skill_name": record.skill_name,
            "target_dir": str(record.target_dir),
            "action": record.action,
            "backup_path": str(record.backup_path) if record.backup_path else None,
            "files_copied": record.files_copied,
            "dry_run": record.dry_run,
            "notes": list(record.notes),
        }

    def _run(
        self,
        kind: str,
        payload: dict[str, object],
        context: JobContext,
    ) -> dict[str, object]:
        reporter: ProgressReporter = CancellationProgressReporter(context)
        if kind == "analyze":
            return self._analyze(payload, reporter)
        return self._build(payload, reporter)

    def _analyze(
        self,
        payload: dict[str, object],
        reporter: ProgressReporter,
    ) -> dict[str, object]:
        sources = self._sources(payload)
        rights_note = self._rights_note(payload)
        data_home = self._data_home(payload)
        adapter = self._llm(payload, data_home)
        use_case = AnalyzeUseCase(data_home=data_home, llm=adapter)
        result = use_case.execute(
            sources,
            collection_id=self._optional_text(payload, "collection_id"),
            rights_note=rights_note,
            on_progress=reporter,
        )
        if result.bundle is None:
            details = "; ".join(error.message for error in result.errors[:3])
            raise DesktopRequestError(details or "No valid sources to analyze.")
        bundle_path = self._save_bundle(result.bundle, sources)
        return {
            "kind": "analyze",
            "bundle_path": str(bundle_path),
            "collection_id": result.bundle.collection_id,
            "source_count": len(result.bundle.source_ids),
            "warnings": [self._gate_error(error) for error in result.errors],
        }

    def _build(
        self,
        payload: dict[str, object],
        reporter: ProgressReporter,
    ) -> dict[str, object]:
        sources = self._sources(payload, required=False)
        from_analysis = self._optional_path(payload, "from_analysis")
        if not sources and from_analysis is None:
            raise DesktopRequestError("Provide sources or an analysis bundle.")
        # Even a reviewed AnalysisBundle is a user-triggered conversion; the
        # desktop surface requires an explicit rights acknowledgement before
        # it starts any compile work.
        rights_note = self._rights_note(payload)
        spec = self._skill_spec(payload)
        data_home = self._data_home(payload)
        adapter = self._llm(payload, data_home) if sources else None
        use_case = BuildUseCase(data_home=data_home, llm=adapter)
        output_dir = self.skill_dir / spec.name
        if from_analysis is not None:
            result = use_case.build_from_bundle(
                from_analysis,
                spec,
                output_dir=output_dir,
                on_progress=reporter,
            )
        else:
            result = use_case.build_from_sources(
                sources,
                spec,
                rights_note=rights_note,
                output_dir=output_dir,
                on_progress=reporter,
            )
        if result.skill_dir is None:
            raise DesktopRequestError("Build produced no Skill directory.")
        bundle_path = (
            self._save_bundle(result.bundle, sources) if result.bundle else None
        )
        return {
            "kind": "build",
            "skill_dir": str(result.skill_dir),
            "bundle_path": str(bundle_path) if bundle_path else None,
            "collection_id": result.collection_id,
            "source_count": len(result.source_manifests),
            "warnings": [self._gate_error(error) for error in result.errors],
            "publication_ready": result.publication_ready,
        }

    def _llm(self, payload: dict[str, object], data_home: Path) -> Any:
        kind = self._optional_text(payload, "llm") or "mock"
        try:
            return build_llm_adapter(
                kind,
                model=self._optional_text(payload, "llm_model"),
                base_url=self._optional_text(payload, "llm_base_url"),
                allow_fallback=bool(payload.get("allow_llm_fallback", False)),
                data_home=data_home,
                llm_profiles=self._optional_text(payload, "llm_profiles"),
                llm_profile=self._optional_text(payload, "llm_profile"),
                llm_strategy=self._optional_text(payload, "llm_strategy") or "single",
                locale="zh-CN",
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            raise DesktopRequestError(str(exc)) from exc

    def _save_bundle(
        self,
        bundle: AnalysisBundle | None,
        sources: list[str],
    ) -> Path | None:
        if bundle is None:
            return None
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        source = Path(sources[0]) if sources else Path(bundle.collection_id)
        stem = self._safe_component(source.stem or source.name)
        if len(sources) > 1:
            stem = f"{stem}_and_{len(sources) - 1}_more"
        target = self.bundle_dir / f"bundle_{stem}.json"
        suffix = 2
        while target.exists():
            target = self.bundle_dir / f"bundle_{stem}_{suffix}.json"
            suffix += 1
        atomic_write(target, bundle.model_dump_json(indent=2))
        return target

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = _INVALID_FILENAME_CHARS.sub("_", value).strip(". ")
        if not cleaned or not cleaned.strip("_"):
            return "analysis"
        if cleaned.upper() in _WINDOWS_RESERVED_FILENAMES:
            cleaned += "_"
        return cleaned[:120].rstrip(". ") or "analysis"

    def _sources(
        self, payload: dict[str, object], *, required: bool = True
    ) -> list[str]:
        raw = payload.get("sources")
        if not isinstance(raw, list):
            if required:
                raise DesktopRequestError("sources must be a non-empty list")
            return []
        sources = [str(item).strip() for item in raw if str(item).strip()]
        if required and not sources:
            raise DesktopRequestError("sources must be a non-empty list")
        return sources

    @staticmethod
    def _rights_note(payload: dict[str, object]) -> str:
        note = str(payload.get("rights_note") or "").strip()
        if not note:
            raise DesktopRequestError("rights_note is required before processing")
        return note

    def _skill_spec(self, payload: dict[str, object]) -> SkillSpec:
        try:
            return SkillSpec(
                name=str(payload.get("name") or "").strip(),
                description=str(payload.get("description") or "").strip(),
                use_when=self._text_list(payload.get("use_when")),
                do_not_use_when=self._text_list(payload.get("do_not_use_when")),
                required_inputs=self._text_list(payload.get("required_inputs"))
                or list(_DEFAULT_REQUIRED_INPUTS),
                outputs=self._text_list(payload.get("outputs"))
                or list(_DEFAULT_OUTPUTS),
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise DesktopRequestError(f"Invalid Skill specification: {exc}") from exc

    @staticmethod
    def _text_list(value: object) -> list[str]:
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _data_home(self, payload: dict[str, object]) -> Path:
        selected = self._optional_path(payload, "data_home")
        if selected is not None:
            return selected
        return self.data_home

    @staticmethod
    def _optional_text(payload: dict[str, object], key: str) -> str | None:
        value = payload.get(key)
        text = str(value).strip() if value is not None else ""
        return text or None

    @staticmethod
    def _optional_path(payload: dict[str, object], key: str) -> Path | None:
        value = payload.get(key)
        if value is None or not str(value).strip():
            return None
        return Path(str(value)).expanduser().resolve()

    def _required_path(
        self,
        payload: dict[str, object],
        key: str,
        *,
        must_exist: bool,
    ) -> Path:
        path = self._optional_path(payload, key)
        if path is None:
            raise DesktopRequestError(f"{key} is required")
        if must_exist and not path.exists():
            raise DesktopRequestError(f"{key} does not exist")
        return path

    @staticmethod
    def _gate_error(error: object) -> dict[str, str]:
        return {
            "code": str(getattr(getattr(error, "code", None), "value", "GATE_ERROR")),
            "message": str(getattr(error, "message", "")),
            "recovery": str(getattr(error, "recovery", "")),
        }


__all__ = ["DesktopApplicationService", "DesktopRequestError"]
