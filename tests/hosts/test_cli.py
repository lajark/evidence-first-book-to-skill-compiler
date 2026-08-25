"""CLI integration tests for the ``install`` and ``uninstall`` commands (TASK-017)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from book2skill.cli import app
from book2skill.runtime.closure import (
    CapabilityKind,
    ClosureLifecycle,
    ClosureResource,
    ProvenanceRef,
    ResourceClass,
    RuntimeClosureManifest,
    ToolCapability,
    write_manifest,
)
from book2skill.runtime.product_manifest import write_product_manifest
from book2skill.runtime.profiles import (
    CoreRuntimeDependency,
    GeneratedSkillProduct,
    HostRuntime,
    ProductProfile,
)

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)

runner = CliRunner()


def _make_skill(skill_dir: Path, name: str = "my-skill") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A valid skill.\n---\n# Body\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("guide\n")
    return skill_dir


def _make_runtime_product_skill(
    skill_dir: Path,
    *,
    lifecycle: ClosureLifecycle = ClosureLifecycle.PRODUCTION,
    product: GeneratedSkillProduct | None = None,
) -> RuntimeClosureManifest:
    _make_skill(skill_dir)
    skill_hash = hashlib.sha256((skill_dir / "SKILL.md").read_bytes()).hexdigest()
    manifest = RuntimeClosureManifest(
        closure_version="1.0.0",
        lifecycle=lifecycle,
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        skill_kernel_id="kernel.plan",
        skill_kernel_version="1.0.0",
        asset_pack_id="pack.plan",
        asset_pack_version="1.0.0",
        io_schema_id="io.plan",
        io_schema_version="1.0.0",
        security_profile_id="standalone-default",
        security_profile_version="1.0.0",
        resources=[
            ClosureResource(
                resource_id="skill:document",
                kind="kernel",
                resource_class=ResourceClass.EXACT_REQUIRED,
                path="SKILL.md",
                version="1.0.0",
                sha256=skill_hash,
                provenance=[
                    ProvenanceRef(source_id="fixture:cli", locator="SKILL.md")
                ],
            )
        ],
    ).with_hash()
    write_manifest(skill_dir, manifest)
    effective_product = product or GeneratedSkillProduct(
        product_id="skill.plan",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile=ProductProfile.STANDALONE,
    )
    write_product_manifest(
        skill_dir,
        effective_product,
        closure_hash=manifest.closure_hash,
    )
    return manifest


class TestInstallCommand:
    def test_install_to_project_host(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "Installed" in result.stdout
        target = project_root / "skills" / "my-skill"
        assert target.exists()
        assert (target / "SKILL.md").exists()

    def test_install_to_codex_with_overlay(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "codex",
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        target = project_root / ".agents" / "skills" / "my-skill"
        assert (target / "SKILL.md").exists()
        assert (target / "agents.md").exists()

    def test_install_dry_run_no_changes(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert "would install" in result.stdout
        # Nothing on disk.
        assert not (project_root / "skills" / "my-skill").exists()

    def test_install_json_output(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["action"] == "install"
        assert data["skill_name"] == "my-skill"
        assert data["dry_run"] is False
        assert data["files_copied"] == 2  # SKILL.md + references/guide.md

    def test_install_invalid_skill_dir_exits_one(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(tmp_path / "missing"),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 1
        assert "INSTALL_SKILL_DIR_INVALID" in result.stdout

    def test_install_unknown_host_rejected(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "unknown",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        # Typer surfaces ValueError as a BadParameter → exit code 2.
        assert result.exit_code != 0
        # The error message may be on stdout or stderr depending on runner
        # configuration; check the combined output.
        assert "Unknown host" in result.output

    def test_install_with_existing_target_takes_backup(
        self, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"
        # Pre-populate target with an "old" version.
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("old\n")
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["backup_path"] is not None
        backup_path = Path(data["backup_path"])
        assert backup_path.exists()
        assert (backup_path / "SKILL.md").read_text() == "old\n"

    def test_runtime_product_manifest_opt_in_reports_closure_hash(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "src"
        closure = _make_runtime_product_skill(skill_dir)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
                "--json",
            ],
        )

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["runtime_product_id"] == "skill.plan"
        assert payload["runtime_closure_hash"] == closure.closure_hash
        assert (
            project_root / "skills" / "my-skill" / "runtime-closure.json"
        ).exists()

    def test_runtime_product_manifest_is_auto_detected(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "src"
        closure = _make_runtime_product_skill(skill_dir)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["runtime_product_id"] == "skill.plan"
        assert payload["runtime_closure_hash"] == closure.closure_hash

    def test_auto_detected_candidate_is_rejected(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "candidate"
        _make_runtime_product_skill(skill_dir, lifecycle=ClosureLifecycle.CANDIDATE)
        project_root = tmp_path / "proj"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
            ],
        )

        assert result.exit_code == 1
        assert "RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED" in result.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_runtime_product_candidate_is_rejected_before_target_mutation(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "candidate"
        _make_runtime_product_skill(skill_dir, lifecycle=ClosureLifecycle.CANDIDATE)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
            ],
        )

        assert result.exit_code == 1
        assert "RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED" in result.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_runtime_product_contract_mismatch_is_rejected_before_target_mutation(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "mismatched"
        product = GeneratedSkillProduct(
            product_id="skill.other",
            task_id="other",
            task_contract_id="task.other",
            task_contract_version="1.0.0",
            profile=ProductProfile.STANDALONE,
        )
        # The helper writes a Closure for task.plan, intentionally mismatching
        # the descriptor's task contract.
        _make_runtime_product_skill(skill_dir, product=product)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
            ],
        )

        assert result.exit_code == 1
        assert "RUNTIME_CLOSURE_INVALID" in result.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_runtime_product_capability_mismatch_is_rejected_before_target_mutation(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "mismatched-capability"
        product = GeneratedSkillProduct(
            product_id="skill.plan",
            task_id="plan",
            task_contract_id="task.plan",
            task_contract_version="1.0.0",
            profile=ProductProfile.STANDALONE,
            capabilities=[
                ToolCapability(
                    capability_id="tool:declared",
                    kind=CapabilityKind.BUNDLED_SCRIPT,
                    version="1.0.0",
                    resource_id="script:declared",
                )
            ],
        )
        # The helper writes a Closure with no capabilities, intentionally
        # mismatching the descriptor's capability declaration.
        _make_runtime_product_skill(skill_dir, product=product)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
            ],
        )

        assert result.exit_code == 1
        assert "RUNTIME_CLOSURE_INVALID" in result.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_runtime_product_closure_hash_mismatch_is_rejected_before_target_mutation(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "mismatched-closure-hash"
        product = GeneratedSkillProduct(
            product_id="skill.plan",
            task_id="plan",
            task_contract_id="task.plan",
            task_contract_version="1.0.0",
            profile=ProductProfile.STANDALONE,
        )
        _make_runtime_product_skill(skill_dir, product=product)
        # Keep the descriptor internally hash-valid but bind it to a different
        # Closure, so the install gate must reject before touching the target.
        write_product_manifest(skill_dir, product, closure_hash="0" * 64)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
            ],
        )

        assert result.exit_code == 1
        assert "RUNTIME_CLOSURE_INVALID" in result.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_runtime_product_without_closure_hash_is_rejected_before_target_mutation(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "missing-closure-hash"
        product = GeneratedSkillProduct(
            product_id="skill.plan",
            task_id="plan",
            task_contract_id="task.plan",
            task_contract_version="1.0.0",
            profile=ProductProfile.STANDALONE,
        )
        _make_runtime_product_skill(skill_dir, product=product)
        write_product_manifest(skill_dir, product)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
            ],
        )

        assert result.exit_code == 1
        assert "PROFILE_MANIFEST_INVALID" in result.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_extension_product_requires_explicit_host_runtime_snapshot(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "extension"
        product = GeneratedSkillProduct(
            product_id="skill.plan.ext",
            task_id="plan",
            task_contract_id="task.plan",
            task_contract_version="1.0.0",
            profile=ProductProfile.EXTENSION_BACKED,
            runtime_dependency=CoreRuntimeDependency(
                core_version="1.0.5",
                sdk_version="1.0.0",
                permissions=("workspace.read",),
            ),
        )
        _make_runtime_product_skill(skill_dir, product=product)
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        missing = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
            ],
        )

        assert missing.exit_code == 1
        assert "PROFILE_CORE_REQUIRED" in missing.output
        assert not (project_root / "skills" / "my-skill").exists()

    def test_extension_product_accepts_matching_host_runtime_snapshot(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "extension"
        product = GeneratedSkillProduct(
            product_id="skill.plan.ext",
            task_id="plan",
            task_contract_id="task.plan",
            task_contract_version="1.0.0",
            profile=ProductProfile.EXTENSION_BACKED,
            runtime_dependency=CoreRuntimeDependency(
                core_version="1.0.5",
                sdk_version="1.0.0",
                permissions=("workspace.read",),
            ),
        )
        closure = _make_runtime_product_skill(skill_dir, product=product)
        host_runtime_path = tmp_path / "host-runtime.json"
        host_runtime_path.write_text(
            HostRuntime(
                core_version="1.0.5",
                sdk_version="1.0.0",
                permissions=("workspace.read",),
            ).model_dump_json(),
            encoding="utf-8",
        )
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--runtime-product-manifest",
                str(skill_dir / "runtime-product.json"),
                "--host-runtime",
                str(host_runtime_path),
                "--json",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert json.loads(result.stdout)["runtime_closure_hash"] == closure.closure_hash


class TestUninstallCommand:
    def test_uninstall_removes_target(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "Uninstalled" in result.stdout
        assert not target.exists()

    def test_uninstall_dry_run_keeps_target(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert target.exists()  # not removed

    def test_uninstall_json_output(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["action"] == "uninstall"
        assert data["skill_name"] == "my-skill"
        assert data["dry_run"] is False

    def test_uninstall_invalid_slug_exits_one(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "Bad_Slug",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 1
        assert "UNINSTALL_FAILED" in result.stdout

    def test_uninstall_absent_target_succeeds(self, tmp_path: Path) -> None:
        """Idempotent: uninstalling an absent skill is a no-op success."""
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "never-installed",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0


class TestInstallUninstallRoundTrip:
    def test_install_then_uninstall(self, tmp_path: Path) -> None:
        """End-to-end: install then uninstall leaves no residue."""
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        # Install
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        target = project_root / "skills" / "my-skill"
        assert target.exists()

        # Uninstall
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert not target.exists()
