"""Create checksums and a traceable manifest for a Windows installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_targets(artifact: Path, output_dir: Path) -> dict[str, Path]:
    """Return all generated deliverables except the checksum file itself."""

    output_root = output_dir.resolve()
    try:
        artifact_name = artifact.relative_to(output_root).as_posix()
    except ValueError:
        artifact_name = artifact.name
    targets: dict[str, Path] = {artifact_name: artifact}
    for candidate in output_root.rglob("*"):
        if not candidate.is_file() or candidate.name == "checksums.sha256":
            continue
        relative = candidate.relative_to(output_root).as_posix()
        previous = targets.get(relative)
        if previous is not None and previous.resolve() != candidate.resolve():
            raise ValueError(f"Duplicate checksum path: {relative}")
        targets[relative] = candidate
    return targets


def _git_commit(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty(repo: Path) -> bool | None:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return bool(output.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def create_manifest(
    *,
    repo: Path,
    artifact: Path,
    version: str,
    output_dir: Path,
    release_ready: bool = False,
    signature_status: str = "unsigned",
    signature_subject: str | None = None,
    signature_thumbprint: str | None = None,
    signature_timestamp_status: str = "not_applicable",
) -> dict[str, object]:
    """Write the installer checksum and return the manifest payload."""

    artifact = artifact.resolve()
    if not artifact.is_file():
        raise FileNotFoundError(f"installer not found: {artifact}")
    license_text = (repo / "LICENSE").read_text(encoding="utf-8")
    private_license = "Private Use Notice" in license_text
    if release_ready and private_license:
        raise RuntimeError(
            "External release is blocked while LICENSE contains the private-use notice."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256(artifact)
    dependency_manifest = output_dir / "dependency-manifest.json"
    dependency_review_required = 0
    if dependency_manifest.is_file():
        dependency_payload = json.loads(
            dependency_manifest.read_text(encoding="utf-8")
        )
        packages = dependency_payload.get("packages", [])
        if isinstance(packages, list):
            dependency_review_required = sum(
                1
                for package in packages
                if isinstance(package, dict)
                and package.get("license_status") == "review_required"
            )
    if release_ready and dependency_review_required:
        raise RuntimeError(
            "External release is blocked while dependency licenses need review "
            f"({dependency_review_required} packages)."
        )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": "book2skill-desktop",
        "version": version,
        "platform": "windows",
        "architecture": "x64",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _git_commit(repo),
        "source_dirty": _git_dirty(repo),
        "release_ready": bool(release_ready and not private_license),
        "license_status": (
            "blocked_private_license" if private_license else "review_required"
        ),
        "signature_status": signature_status,
        "signature_timestamp_status": signature_timestamp_status,
        "artifact": {"file": artifact.name, "sha256": digest},
        "dependency_manifest": (
            {
                "file": dependency_manifest.name,
                "sha256": _sha256(dependency_manifest),
            }
            if dependency_manifest.is_file()
            else None
        ),
        "dependency_review_required": dependency_review_required,
    }
    if signature_subject:
        manifest["signature_subject"] = signature_subject
    if signature_thumbprint:
        manifest["signature_thumbprint"] = signature_thumbprint
    (output_dir / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_lines = [
        f"{_sha256(path)}  {relative}"
        for relative, path in _checksum_targets(artifact, output_dir).items()
    ]
    (output_dir / "checksums.sha256").write_text(
        "\n".join(sorted(checksum_lines)) + "\n", encoding="utf-8"
    )
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-ready", action="store_true")
    parser.add_argument("--signature-status", default="unsigned")
    parser.add_argument("--signature-subject")
    parser.add_argument("--signature-thumbprint")
    parser.add_argument("--signature-timestamp-status", default="not_applicable")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        create_manifest(
            repo=args.repo.resolve(),
            artifact=args.artifact,
            version=args.version,
            output_dir=args.output_dir,
            release_ready=args.release_ready,
            signature_status=args.signature_status,
            signature_subject=args.signature_subject,
            signature_thumbprint=args.signature_thumbprint,
            signature_timestamp_status=args.signature_timestamp_status,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"desktop release metadata written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
