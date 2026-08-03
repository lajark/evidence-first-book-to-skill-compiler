"""Assemble the official ``book2skill-core-<semver>.zip`` release package.

The layout follows ``RELEASE_PACKAGE_SPEC.md``: a VERSION marker, the Wheel
under ``dist/``, the Book2Skill meta-Skill under ``skills/``, public contracts
under ``contracts/``, the SDK surface under ``sdk/``, a sample-extension fixture
under ``examples/``, plus an ``install.py`` and a schema-conformant
``release-manifest.json``. ``checksums.sha256`` covers every payload file and
the outer ``.sha256`` verifies the ZIP itself.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from book2skill import __version__
from book2skill.extensions.package import (
    CHECKSUMS_FILENAME,
)

RELEASE_NAME = "book2skill-core"
_ARTIFACT_DIRS = ("dist", "skills", "contracts", "sdk", "examples", "LICENSES")


class ReleaseError(Exception):
    """Raised when the release package cannot be assembled."""


def build_release(
    *,
    repo_root: str | Path,
    dest_dir: str | Path,
    version: str | None = None,
    wheel: str | Path | None = None,
    skill_dir: str | Path | None = None,
) -> Path:
    """Assemble a release ZIP + ``.sha256`` and return the ZIP path.

    Args:
        repo_root: Repository root providing contracts, docs and templates.
        dest_dir: Directory to write the ``.zip`` and ``.sha256`` outputs.
        version: Version string; defaults to the installed Core version.
        wheel: Optional built wheel to include under ``dist/``.
        skill_dir: Optional meta-Skill directory to package under ``skills/``.

    Returns:
        Path to the assembled ``book2skill-core-<version>.zip``.
    """
    from book2skill.packaging.sample_extension import write_sample_extension

    repo = Path(repo_root).resolve()
    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    version = version or __version__

    with _staging() as td:
        pkg_root = Path(td) / f"{RELEASE_NAME}-{version}"
        pkg_root.mkdir(parents=True, exist_ok=True)

        _copy_wheel(pkg_root, wheel)
        _copy_skill_zip(pkg_root, skill_dir)
        _copy_contracts(repo, pkg_root)
        _copy_sdk_surface(repo, pkg_root)
        write_sample_extension(pkg_root / "examples" / "sample-extension")
        _copy_static(repo, pkg_root, version)
        _write_install_script(pkg_root)

        artifacts = _collect_payload(pkg_root)
        _write_release_manifest(pkg_root, version, artifacts)
        # Recompute manifest-inclusive payload for the final checksums file
        # (release-manifest.json is itself a delivery file).  Reuse the
        # digests collected above rather than materialising every payload a
        # second time just to make checksums.sha256.
        known_hashes = {item["path"]: item["sha256"] for item in artifacts}
        known_hashes["release-manifest.json"] = _sha256_of_file(
            pkg_root / "release-manifest.json"
        )
        _write_checksums(pkg_root, known_hashes=known_hashes)

        zip_path = dest / f"{RELEASE_NAME}-{version}.zip"
        _zip_tree(pkg_root, zip_path)
        sha256 = _sha256_of_file(zip_path)
        (dest / f"{RELEASE_NAME}-{version}.zip.sha256").write_text(
            sha256 + "\n", encoding="utf-8"
        )
        return zip_path


# ---------------------------------------------------------------------------
# Assembly steps
# ---------------------------------------------------------------------------


def _copy_wheel(pkg_root: Path, wheel: str | Path | None) -> None:
    """Include a built wheel under ``dist/`` if one was provided."""
    if wheel is None:
        return
    src = Path(wheel)
    if not src.is_file():
        raise ReleaseError(f"wheel not found: {src}")
    target = pkg_root / "dist"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target / src.name)


def _copy_skill_zip(pkg_root: Path, skill_dir: str | Path | None) -> None:
    """Zip the Book2Skill meta-Skill into ``skills/book2skill-skill.zip``."""
    if skill_dir is None:
        return
    src = Path(skill_dir)
    if not src.is_dir():
        raise ReleaseError(f"meta-skill dir not found: {src}")
    target = pkg_root / "skills"
    target.mkdir(parents=True, exist_ok=True)
    zip_path = target / "book2skill-skill.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())


def _copy_contracts(repo_root: Path, pkg_root: Path) -> None:
    """Copy the versioned JSON Schema contracts under ``contracts/``."""
    schemas = repo_root / "schemas"
    if not schemas.is_dir():
        raise ReleaseError(f"schemas dir not found: {schemas}")
    target = pkg_root / "contracts"
    target.mkdir(parents=True, exist_ok=True)
    for f in sorted(schemas.glob("*.json")):
        shutil.copy2(f, target / f.name)
    (target / "compatibility.md").write_text(
        "# Compatibility\n\n"
        "Public contracts are versioned by their `schema_version` field.\n"
        "Downstream extensions must only depend on the SDK surface and these "
        "contracts.\n",
        encoding="utf-8",
    )


def _copy_sdk_surface(repo_root: Path, pkg_root: Path) -> None:
    """Ship the SDK doc plus a machine snapshot of the public API surface."""
    target = pkg_root / "sdk"
    target.mkdir(parents=True, exist_ok=True)
    sdk_doc = repo_root / "EXTENSION_SDK.md"
    if sdk_doc.is_file():
        shutil.copy2(sdk_doc, target / "EXTENSION_SDK.md")
    target.joinpath("api-surface.json").write_text(
        json.dumps({"names": sorted(_sdk_public_names())}, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_static(repo: Path, pkg_root: Path, version: str) -> None:
    """Write VERSION marker and copy license / notices where present."""
    (pkg_root / "VERSION").write_text(version + "\n", encoding="utf-8")
    lic = repo / "LICENSE"
    if lic.is_file():
        (pkg_root / "LICENSE").write_text(
            lic.read_text(encoding="utf-8"), encoding="utf-8"
        )
    for rel in ("LICENSES", "THIRD_PARTY_NOTICES.md", "ACKNOWLEDGMENTS.md"):
        src = repo / rel
        if not src.exists():
            continue
        dest = pkg_root / rel
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    # Ship the LLM config template + guide so deployments can wire up cloud
    # models without cloning the source repo. See RELEASE_PACKAGE_SPEC §"LLM".
    env_template = repo / ".env.example"
    if env_template.is_file():
        shutil.copy2(env_template, pkg_root / ".env.example")
    llm_guide = repo / "docs" / "LLM_CONFIG.md"
    if llm_guide.is_file():
        docs_target = pkg_root / "docs"
        docs_target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(llm_guide, docs_target / "LLM_CONFIG.md")
    (pkg_root / "README_INSTALL.md").write_text(
        _README_INSTALL_TEMPLATE.format(version=version), encoding="utf-8"
    )
    (pkg_root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version}\n\n- Initial Core release package.\n",
        encoding="utf-8",
    )


def _collect_payload(pkg_root: Path) -> list[dict[str, str]]:
    """Return ``{path, sha256}`` for every file under *pkg_root*."""
    artifacts = []
    for p in sorted(pkg_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(pkg_root).as_posix()
            artifacts.append({"path": rel, "sha256": _sha256_of_file(p)})
    return artifacts


def _write_release_manifest(
    pkg_root: Path, version: str, artifacts: list[dict[str, str]]
) -> None:
    """Emit the schema-conformant release-manifest.json."""
    manifest = {
        "schema_version": 1,
        "product": "book2skill-core",
        "version": version,
        "python_requires": ">=3.11",
        "sdk_version": _sdk_version(),
        "extension_schema_versions": [1],
        "artifacts": artifacts,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "licenses": ["MIT"],
    }
    (pkg_root / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_checksums(pkg_root: Path, *, known_hashes: dict[str, str]) -> None:
    """Stream checksum collection, reusing hashes already computed for release."""
    lines: list[str] = []
    for p in sorted(pkg_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(pkg_root).as_posix()
            # Only the top-level checksums file is self-excluded; nested
            # package checksums (e.g. the sample-extension) are payload.
            if rel == CHECKSUMS_FILENAME:
                continue
            digest = known_hashes.get(rel) or _sha256_of_file(p)
            lines.append(f"{digest}  {rel}")
    (pkg_root / CHECKSUMS_FILENAME).write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def _write_install_script(pkg_root: Path) -> None:
    """Write a minimal, cross-platform ``install.py`` for the package."""
    (pkg_root / "install.py").write_text(
        _INSTALL_SCRIPT_TEMPLATE, encoding="utf-8"
    )


def _zip_tree(src: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def _sdk_public_names() -> list[str]:
    try:
        import book2skill.sdk as sdk

        return list(sdk.__all__)
    except ImportError:  # pragma: no cover - only when SDK is unimportable
        return []


def _sdk_version() -> str:
    try:
        from book2skill import __version__ as v

        return v
    except ImportError:  # pragma: no cover
        return "unknown"


def _staging() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix=f"{RELEASE_NAME}-")


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Embedded templates
# ---------------------------------------------------------------------------

_README_INSTALL_TEMPLATE = """# Book2Skill Core {version}

Official release package. Validate, extract, then run the installer **only
after you have reviewed the manifest and checksums**.

## Install

1. Verify the ZIP hash against `book2skill-core-{version}.zip.sha256`.
2. Extract to a fresh directory (never run from inside the archive).
3. Review `release-manifest.json`, `LICENSE`, `THIRD_PARTY_NOTICES.md`.
4. `python install.py` (or `python3 install.py`) to create a dedicated
   virtualenv under `$BOOK2SKILL_HOME/venv` (default `~/.book2skill/venv`)
   and install the bundled Wheel. Set `BOOK2SKILL_HOME=<dir>` to deploy
   somewhere other than the user home.
5. Verify the install (`.env` not yet required — defaults to Mock mode):
   - Windows: `venv\\Scripts\\book2skill version`
   - POSIX:   `venv/bin/book2skill version`

## 接入大模型（可选，默认为 Mock 离线模式）

要使用云端 LLM（OpenAI / 阿里云百炼 / Azure / Ollama 等任何 OpenAI 兼容
端点），需补装 `llm` 可选依赖并配置 `.env`。本包根目录已随附
`.env.example` 模板，完整字段与优先级说明见 `docs/LLM_CONFIG.md`。

### 1. 补装 LLM 依赖（在安装好的 venv 内）

```
<venv-python> -m pip install "book2skill[llm]" \\
  -i https://pypi.tuna.tsinghua.edu.cn/simple \\
  --trusted-host pypi.tuna.tsinghua.edu.cn
```

> `<venv-python>` 为 `BOOK2SKILL_HOME/venv/Scripts/python.exe`（Windows）或
> `BOOK2SKILL_HOME/venv/bin/python`（POSIX）。亦可省略镜像参数使用官方 PyPI。

### 2. 从模板创建 `.env`

```
cp .env.example .env          # POSIX
copy .env.example .env        # Windows CMD
```

### 3. 编辑 `.env` 填入端点与密钥

```ini
BOOK2SKILL_LLM=compatible
LLM_API_KEY=sk-你的密钥
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

完整变量表、本地部署（LM Studio / Ollama / vLLM）与安全注意事项见
`docs/LLM_CONFIG.md`。

### 4. 运行（`.env` 已配好，无需任何 `--llm*` 参数）

```
book2skill analyze book.pdf --json
```

> **位置说明**：`.env` 由内置解析器从当前工作目录向上逐级查找，放在运行
> `book2skill` 的目录（或其任一父目录）即可生效。`.env` 不写入进程环境变量
> 与日志，已被 `.gitignore` 排除不入库；API Key 仅接受环境变量或 `.env`，
> 不接受命令行参数。优先级：命令行非敏感参数 > 系统环境变量（`LLM_*` >
> `OPENAI_*`）> `.env` > 默认 Mock。
"""

_INSTALL_SCRIPT_TEMPLATE = '''"""Book2Skill Core installer (FR-12).

Creates a dedicated virtualenv under ``$BOOK2SKILL_HOME/venv`` (default
``~/.book2skill/venv``) and installs the bundled Wheel into it. Set
``BOOK2SKILL_HOME`` to deploy somewhere other than the user home.
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    wheels = sorted((HERE / "dist").glob("*.whl"))
    if not wheels:
        print("No .whl found under dist/; nothing to install.", file=sys.stderr)
        return 1
    wheel = wheels[0]

    home = os.environ.get("BOOK2SKILL_HOME") or str(Path.home() / ".book2skill")
    root = Path(home)
    env_dir = root / "venv"
    if not (env_dir / "Scripts" / "python.exe").exists() and not (
        env_dir / "bin" / "python"
    ).exists():
        print(f"Creating virtual environment at {env_dir}")
        venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    print(f"Installing {wheel.name}")
    # Use subprocess (not os.system) so quoted Windows paths reach pip intact;
    # cmd.exe mangles the quoted executable path and reports a syntax error.
    try:
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheel)],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"pip install failed (exit {exc.returncode})", file=sys.stderr)
        return exc.returncode
    print(f"Ready. Activate via: {env_dir}")
    print("Run: book2skill doctor  |  book2skill extensions list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
