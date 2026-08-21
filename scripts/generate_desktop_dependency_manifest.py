"""Generate an offline dependency/license manifest for the Windows desktop build."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import deque
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path, PurePosixPath

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT_PACKAGES = (
    "book2skill",
    "pywebview",
    "pypdf",
    "pdfminer.six",
    "beautifulsoup4",
    "python-docx",
    "openai",
    "pyinstaller",
)

# ``clr-loader`` predates PEP 639 metadata in the build environment, but its
# bundled LICENSE file identifies the project license unambiguously.  Keep the
# exception explicit so a missing metadata field cannot silently become an
# unreviewed dependency in a future build.
LICENSE_REVIEW_OVERRIDES = {
    "clr-loader": {
        "license_expression": "MIT",
        "review_basis": "bundled LICENSE file identifies the MIT License",
    }
}


def _active_requirement(requirement: Requirement) -> bool:
    marker = requirement.marker
    return marker is None or marker.evaluate(default_environment())


def _license_files(dist) -> list[str]:
    files = []
    for path in dist.files or ():
        name = path.name.lower()
        if name.startswith(("license", "copying", "notice")):
            files.append(str(path))
    return sorted(files)


def _safe_license_path(raw_path: str) -> Path:
    """Convert a distribution-relative path without allowing traversal."""

    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError(f"Unsafe license path in package metadata: {raw_path}")
    return Path(*relative.parts)


def _copy_license_evidence(
    dist, package_name: str, output_dir: Path
) -> list[str]:
    """Copy declared license files into a portable, package-local evidence tree."""

    package_dir = output_dir / canonicalize_name(package_name)
    artifacts: list[str] = []
    for raw_path in _license_files(dist):
        relative = _safe_license_path(raw_path)
        source = Path(dist.locate_file(PurePosixPath(raw_path)))
        if not source.is_file():
            continue
        destination = package_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        artifacts.append((Path(canonicalize_name(package_name)) / relative).as_posix())
    return sorted(artifacts)


def collect_manifest(license_output_dir: Path | None = None) -> dict[str, object]:
    """Resolve the active runtime/build dependency closure from installed metadata."""

    pending = deque(canonicalize_name(name) for name in ROOT_PACKAGES)
    visited: set[str] = set()
    packages: list[dict[str, object]] = []

    while pending:
        package_name = pending.popleft()
        if package_name in visited:
            continue
        visited.add(package_name)
        try:
            dist = distribution(package_name)
        except PackageNotFoundError as exc:
            raise RuntimeError(
                f"Dependency '{package_name}' is not installed in the build "
                "environment."
            ) from exc

        legacy_license = (dist.metadata.get("License") or "").strip() or None
        license_expression = (
            (dist.metadata.get("License-Expression") or "").strip() or None
        )
        override = LICENSE_REVIEW_OVERRIDES.get(package_name, {})
        license_expression = override.get("license_expression", license_expression)
        license_name = legacy_license or license_expression
        classifiers = [
            value.removeprefix("License :: ")
            for value in dist.metadata.get_all("Classifier") or []
            if value.startswith("License :: ")
        ]
        was_metadata_unclassified = not legacy_license and not classifiers
        package_entry: dict[str, object] = {
            "name": dist.metadata.get("Name") or package_name,
            "version": dist.version,
            "license": license_name,
            "license_expression": license_expression,
            "license_classifiers": classifiers,
            "license_files": _license_files(dist),
            "license_artifacts": (
                _copy_license_evidence(dist, package_name, license_output_dir)
                if license_output_dir is not None
                else []
            ),
            "license_status": (
                "confirmed"
                if was_metadata_unclassified and license_expression
                else "declared"
                if license_name or classifiers
                else "review_required"
            ),
            "license_review_basis": (
                override.get("review_basis")
                if override
                else "PEP 639 License-Expression metadata plus bundled license files"
                if license_expression
                else None
            ),
        }
        packages.append(package_entry)
        for raw_requirement in dist.requires or ():
            requirement = Requirement(raw_requirement)
            if _active_requirement(requirement):
                pending.append(canonicalize_name(requirement.name))

    packages.sort(key=lambda item: str(item["name"]).lower())
    return {
        "schema_version": 1,
        "profile": "desktop-safe+build-windows",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": list(ROOT_PACKAGES),
        "license_evidence_root": (
            "dependency-licenses" if license_output_dir is not None else None
        ),
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        license_output_dir = args.output.parent / "dependency-licenses"
        payload = collect_manifest(license_output_dir=license_output_dir)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"desktop dependency manifest written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
