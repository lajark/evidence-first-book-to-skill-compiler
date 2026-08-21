from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from book2skill.desktop.server import DesktopWebServer


class FakeService:
    def bootstrap(self) -> dict[str, object]:
        return {"product": "book2skill", "version": "test", "job": {"busy": False}}

    def start_job(self, kind: str, payload: dict[str, object]) -> str:
        assert kind == "analyze"
        assert payload["rights_note"]
        return "job-test"

    def cancel_job(self) -> bool:
        return True

    def install(self, payload: dict[str, object]) -> dict[str, object]:
        return {"dry_run": True}


@pytest.fixture()
def server() -> DesktopWebServer:
    instance = DesktopWebServer(
        service=FakeService(),
        static_dir=Path("src/book2skill/desktop/static"),
    )
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


def _request(
    server: DesktopWebServer,
    path: str,
    *,
    method: str = "GET",
    body: object | None = None,
    token: str | None = None,
):
    data = None if body is None else json.dumps(body).encode("utf-8")
    query = f"?token={token}" if token else ""
    request = urllib.request.Request(
        server.base_url + path + query,
        data=data,
        method=method,
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("X-Book2Skill-Token", token)
    return urllib.request.urlopen(request, timeout=2)


def test_server_requires_session_token(server: DesktopWebServer) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(server.base_url + "/api/bootstrap", timeout=2)
    assert exc_info.value.code == 403


def test_server_bootstrap_and_job_routes(server: DesktopWebServer) -> None:
    with _request(server, "/api/bootstrap", token=server.token) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["product"] == "book2skill"

    with _request(
        server,
        "/api/jobs/analyze",
        method="POST",
        token=server.token,
        body={"sources": ["book.txt"], "rights_note": "licensed"},
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload == {"accepted": True, "job_id": "job-test"}


def test_server_serves_static_assets_without_exposing_api(
    server: DesktopWebServer,
) -> None:
    with _request(server, "/style.css") as response:
        body = response.read().decode("utf-8")
    assert "--accent" in body

    with _request(server, "/") as response:
        html = response.read().decode("utf-8")
    assert 'data-page="function"' in html
    assert 'data-page="settings"' in html
    assert 'data-page="help"' in html


def test_server_rejects_invalid_json(server: DesktopWebServer) -> None:
    request = urllib.request.Request(
        server.url + "/api/jobs/analyze",
        data=b"not-json",
        method="POST",
        headers={
            "X-Book2Skill-Token": server.token,
            "Content-Type": "application/json",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request, timeout=2)
    assert exc_info.value.code == 400
