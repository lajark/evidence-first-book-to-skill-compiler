"""Product descriptor round-trip and fail-closed tests (B2S-M13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book2skill.domain.errors import ErrorCode
from book2skill.runtime import (
    HostRuntime,
    ProductManifestError,
    ProductProfile,
    load_host_runtime,
    load_product_manifest,
    write_product_manifest,
)
from book2skill.runtime.profiles import GeneratedSkillProduct


def _product() -> GeneratedSkillProduct:
    return GeneratedSkillProduct(
        product_id="skill.plan",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile=ProductProfile.STANDALONE,
    )


def test_product_manifest_round_trip_is_hash_addressed(tmp_path: Path) -> None:
    written = write_product_manifest(tmp_path, _product())

    loaded = load_product_manifest(written)

    assert written == tmp_path / "runtime-product.json"
    assert loaded.product == _product()
    assert loaded.manifest_hash == loaded.compute_hash()
    assert load_product_manifest(tmp_path).model_dump(mode="json") == loaded.model_dump(
        mode="json"
    )


def test_product_manifest_tamper_fails_without_echoing_payload(tmp_path: Path) -> None:
    written = write_product_manifest(tmp_path, _product())
    payload = json.loads(written.read_text(encoding="utf-8"))
    payload["product"]["product_id"] = "tampered-secret"
    written.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProductManifestError) as exc_info:
        load_product_manifest(written)

    assert exc_info.value.code == ErrorCode.PROFILE_MANIFEST_INVALID
    assert "tampered-secret" not in str(exc_info.value)


@pytest.mark.parametrize("filename", ["../runtime-product.json", "C:\\product.json"])
def test_product_manifest_rejects_escaping_filename(
    tmp_path: Path, filename: str
) -> None:
    with pytest.raises(ProductManifestError) as exc_info:
        write_product_manifest(tmp_path, _product(), filename=filename)

    assert exc_info.value.code == ErrorCode.PROFILE_MANIFEST_INVALID


def test_host_runtime_snapshot_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "host-runtime.json"
    expected = HostRuntime(
        core_version="1.0.5",
        sdk_version="1.0.0",
        permissions=("workspace.read",),
        capabilities={"host.search": "2.0.0"},
    )
    path.write_text(expected.model_dump_json(), encoding="utf-8")

    assert load_host_runtime(path) == expected
