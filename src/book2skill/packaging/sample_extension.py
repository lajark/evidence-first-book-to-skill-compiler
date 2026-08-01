"""Write a minimal ``sample-extension`` fixture into the release package.

The sample exercises the FR-11 contract end-to-end: a valid manifest, a
checksums file, and a dependency-free ``activate`` entry point. Downstream
projects use it to verify their install/upgrade/rollback/uninstall pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from book2skill.extensions.package import build_checksums_text

SAMPLE_EXTENSION_ID = "sample-ext"
SAMPLE_EXTENSION_VERSION = "1.0.0"

_MANIFEST = {
    "schema_version": 1,
    "extension_id": SAMPLE_EXTENSION_ID,
    "version": SAMPLE_EXTENSION_VERSION,
    "requires": {"book2skill": ">=0.1.0,<1.0.0", "extensions": []},
    "entry_points": ["sample_ext.extension:activate"],
    "contributes": {"commands": ["sample"], "skills": []},
    "permissions": ["read_normalized_sources", "write_extension_data"],
    "checksums_file": "checksums.sha256",
}

_ACTIVATION = """\
from book2skill.sdk import ExtensionContext


def activate(ctx: ExtensionContext) -> None:
    \"\"\"Exercise the SDK surface during activation.\"\"\"
    ctx.require_permission("read_normalized_sources")
"""


def write_sample_extension(target: Path) -> Path:
    """Materialise the sample-extension package into *target* and return it."""
    target.mkdir(parents=True, exist_ok=True)
    files = {
        "extension-manifest.json": json.dumps(_MANIFEST),
        "sample_ext/extension.py": _ACTIVATION,
    }
    encoded: dict[str, bytes] = {}
    for rel, data in files.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data.encode("utf-8"))
        encoded[rel] = data.encode("utf-8")
    (target / "checksums.sha256").write_text(
        build_checksums_text(encoded), encoding="utf-8"
    )
    return target
