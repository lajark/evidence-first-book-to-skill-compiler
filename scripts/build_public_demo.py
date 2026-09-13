"""Build and verify the repository-authored public demonstration Skill.

The demo deliberately uses the Mock LLM and writes only to a caller-selected
directory. It is safe to run from a clean checkout and never reads API keys.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "examples" / "evidence-first-demo" / "input.md"
INTEGRITY_CHECK = REPO_ROOT / "scripts" / "check_generated_skill_integrity.py"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify the offline Evidence-first Book2Skill demo."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".workspace/tmp/public-demo"),
        help="Exact empty directory for bundles, workspace, and generated Skill.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable summary instead of a short human summary.",
    )
    return parser


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _source_manifests(workspace: Path, target: Path) -> Path:
    manifests = []
    for path in sorted(workspace.glob("raw/*/1/manifest.json")):
        manifests.append(json.loads(path.read_text(encoding="utf-8")))
    if not manifests:
        raise RuntimeError("Build produced no authoritative source manifest")
    manifest_path = target / "source-manifests.json"
    manifest_path.write_text(
        json.dumps({"sources": manifests}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def build_demo(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(
            "Refusing to overwrite non-empty output directory: "
            f"{output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = output_dir / "skill"
    bundle_dir = output_dir / "bundles"
    workspace_dir = output_dir / "workspace"
    command = [
        sys.executable,
        "-m",
        "book2skill.cli",
        "build",
        str(SOURCE),
        "--name",
        "evidence-first-demo",
        "--description",
        "Compile an original note into a traceable reviewable Skill",
        "--use-when",
        "When demonstrating source evidence, review, and deterministic replay",
        "--no-use-when",
        "When asked to reproduce the source document verbatim",
        "--required-input",
        "A repository-authored evidence note or a verified analysis bundle",
        "--output",
        "A concise source-traceable decision record",
        "--llm",
        "mock",
        "--output-dir",
        str(skill_dir),
        "--bundle-dir",
        str(bundle_dir),
        "--data-home",
        str(workspace_dir),
        "--rights-note",
        "Repository-authored demo text; no third-party content",
        "--json",
    ]
    build = _run(command)
    if build.returncode != 0:
        raise RuntimeError(
            "Public demo build failed:\n"
            + (build.stderr or build.stdout or "no diagnostic output")
        )
    try:
        build_summary = json.loads(build.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Public demo build did not return JSON") from exc

    manifests = _source_manifests(workspace_dir, output_dir)
    integrity = _run(
        [
            sys.executable,
            str(INTEGRITY_CHECK),
            str(skill_dir),
            "--source-manifests",
            str(manifests),
        ]
    )
    if integrity.returncode != 0:
        raise RuntimeError(
            "Public demo integrity check failed:\n"
            + (integrity.stderr or integrity.stdout or "no diagnostic output")
        )
    integrity_summary = json.loads(integrity.stdout)
    if integrity_summary.get("blocked"):
        raise RuntimeError("Public demo integrity gate returned blocked=true")
    normalized = json.loads(
        (skill_dir / "normalized-bundle.json").read_text(encoding="utf-8")
    )
    source_ids = sorted(
        {
            ref.get("source_id")
            for unit in normalized.get("units", [])
            if isinstance(unit, dict)
            for ref in unit.get("source_refs", [])
            if isinstance(ref, dict) and ref.get("source_id")
        }
    )
    locators = sorted(
        {
            ref.get("block_id")
            for unit in normalized.get("units", [])
            if isinstance(unit, dict)
            for ref in unit.get("source_refs", [])
            if isinstance(ref, dict) and ref.get("block_id")
        }
    )
    return {
        "source": str(SOURCE.relative_to(REPO_ROOT)),
        "output_dir": str(output_dir),
        "skill_dir": str(skill_dir),
        "bundle_path": build_summary.get("bundle_path"),
        "source_count": build_summary.get("source_count"),
        "evidence_chain": {"source_ids": source_ids, "block_ids": locators},
        "integrity": {
            "artifact_ok": integrity_summary.get("artifact_ok"),
            "content_report_match": integrity_summary.get("content_report_match"),
            "authoritative_source_check": integrity_summary.get(
                "authoritative_source_check"
            ),
            "blocked": integrity_summary.get("blocked"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = build_demo(args.output_dir)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"public demo failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Skill: {summary['skill_dir']}")
        print(
            "Evidence: "
            f"{len(summary['evidence_chain']['source_ids'])} source(s), "
            f"{len(summary['evidence_chain']['block_ids'])} block locator(s)"
        )
        print("Integrity: passed (artifact, content report, and source manifest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
