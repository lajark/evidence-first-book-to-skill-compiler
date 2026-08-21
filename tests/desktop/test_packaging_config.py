from __future__ import annotations

from pathlib import Path


def test_windows_spec_excludes_high_risk_optional_pdf_backend() -> None:
    spec = Path("packaging/windows/Book2Skill.spec").read_text(encoding="utf-8")
    assert '"pymupdf"' in spec
    assert '"docling"' in spec
    assert "book2skill/desktop/static" in spec
    assert 'name="Book2Skill"' in spec
    assert '"ebooklib"' in spec


def test_inno_setup_is_per_user_and_keeps_user_data_separate() -> None:
    script = Path("packaging/windows/Book2Skill.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in script
    assert "DefaultDirName={code:Book2SkillDefaultDir}" in script
    assert "GetEnv('LOCALAPPDATA')" in script
    assert "THIRD_PARTY_NOTICES.md" in script
    assert "dependency-manifest.json" in script
    assert "dependency-licenses\\*" in script
    assert "CloseApplications=yes" in script


def test_desktop_release_metadata_blocks_private_license(tmp_path: Path) -> None:
    from scripts.generate_desktop_release import create_manifest

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("Private Use Notice", encoding="utf-8")
    artifact = tmp_path / "Book2Skill.exe"
    artifact.write_bytes(b"installer")

    manifest = create_manifest(
        repo=repo,
        artifact=artifact,
        version="1.0.1",
        output_dir=tmp_path / "metadata",
    )
    assert manifest["release_ready"] is False
    assert manifest["license_status"] == "blocked_private_license"
    assert manifest["source_dirty"] is True
    assert manifest["dependency_manifest"] is None
    assert manifest["dependency_review_required"] == 0
    checksums = (tmp_path / "metadata" / "checksums.sha256").read_text()
    assert "Book2Skill.exe" in checksums
    assert "release-manifest.json" in checksums


def test_desktop_release_checksums_cover_license_evidence(tmp_path: Path) -> None:
    from scripts.generate_desktop_release import create_manifest

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT", encoding="utf-8")
    artifact = tmp_path / "Book2Skill.exe"
    artifact.write_bytes(b"installer")
    output_dir = tmp_path / "metadata"
    evidence = output_dir / "dependency-licenses" / "demo"
    evidence.mkdir(parents=True)
    evidence_file = evidence / "LICENSE.txt"
    evidence_file.write_text("license evidence", encoding="utf-8")

    create_manifest(
        repo=repo,
        artifact=artifact,
        version="1.0.1",
        output_dir=output_dir,
    )
    checksums = (output_dir / "checksums.sha256").read_text(encoding="utf-8")
    assert "dependency-licenses/demo/LICENSE.txt" in checksums


def test_desktop_release_ready_allows_unsigned_open_source_build(
    tmp_path: Path,
) -> None:
    from scripts.generate_desktop_release import create_manifest

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT", encoding="utf-8")
    artifact = tmp_path / "Book2Skill.exe"
    artifact.write_bytes(b"installer")

    manifest = create_manifest(
        repo=repo,
        artifact=artifact,
        version="1.0.1",
        output_dir=tmp_path / "metadata",
        release_ready=True,
    )
    assert manifest["release_ready"] is True
    assert manifest["signature_status"] == "unsigned"
    assert manifest["signature_timestamp_status"] == "not_applicable"


def test_windows_build_script_prefers_project_virtual_environment() -> None:
    script = Path("scripts/build_windows.ps1").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in script
    assert "python -ErrorAction SilentlyContinue" in script
    assert "-CertificateThumbprint" in script
    assert "-CertificatePath" in script
    assert "-TimestampUrl" in script
    assert "pwsh.exe" in script
    assert "sign_windows_release.ps1" in script
    assert '"--signature-status", "unsigned"' in script


def test_windows_signing_script_requires_explicit_untrusted_opt_in() -> None:
    script = Path("scripts/sign_windows_release.ps1").read_text(encoding="utf-8")
    assert '"/fd", "SHA256"' in script
    assert "AllowUntrusted" in script
    assert "BOOK2SKILL_SIGNING_PASSWORD" in script
    assert "self_signed_untrusted" in script
    assert '"/tr"' in script
    assert '"/td", "SHA256"' in script
    assert "RFC 3161" in script


def test_windows_installer_smoke_script_is_isolated_and_cleans_up() -> None:
    script = Path("scripts/verify_windows_installer.ps1").read_text(encoding="utf-8")
    assert "/VERYSILENT" in script
    assert "/NOICONS" in script
    assert "/DIR=$installRootPath" in script
    assert "/LOG=$logPath" in script
    assert "unins000.exe" in script
    assert "Book2Skill-installer-smoke-" in script
    assert "KeepInstall" in script
    assert "AllowExplicitRoot" in script
    assert "LaunchSmokeSeconds" in script
    assert "launch_smoke_status" in script


def test_dependency_manifest_resolves_installed_closure() -> None:
    from scripts.generate_desktop_dependency_manifest import collect_manifest

    manifest = collect_manifest()
    names = {str(item["name"]).lower() for item in manifest["packages"]}
    assert "pywebview" in names
    assert "pyinstaller" in names
    assert "ebooklib" not in names
    assert manifest["profile"] == "desktop-safe+build-windows"
    assert not [
        package
        for package in manifest["packages"]
        if package["license_status"] == "review_required"
    ]
    clr_loader = next(
        package for package in manifest["packages"] if package["name"] == "clr_loader"
    )
    assert clr_loader["license_expression"] == "MIT"
    assert clr_loader["license_status"] == "confirmed"


def test_dependency_manifest_bundles_license_evidence(tmp_path: Path) -> None:
    from scripts.generate_desktop_dependency_manifest import collect_manifest

    evidence_root = tmp_path / "dependency-licenses"
    manifest = collect_manifest(license_output_dir=evidence_root)
    assert manifest["license_evidence_root"] == "dependency-licenses"
    package_with_license = next(
        package for package in manifest["packages"] if package["license_artifacts"]
    )
    for relative in package_with_license["license_artifacts"]:
        assert (evidence_root / Path(relative)).is_file()
