"""Local HTTP/SSE server backing the Windows WebGUI."""

from __future__ import annotations

import json
import mimetypes
import queue
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from book2skill.desktop.jobs import JobBusyError
from book2skill.desktop.service import DesktopApplicationService, DesktopRequestError

MAX_JSON_BYTES = 1 << 20


class _ServerContext:
    def __init__(
        self,
        service: DesktopApplicationService,
        static_dir: Path,
        token: str,
    ) -> None:
        self.service = service
        self.static_dir = static_dir.resolve()
        self.token = token


class _DesktopHTTPServer(ThreadingHTTPServer):
    context: _ServerContext


class _Handler(BaseHTTPRequestHandler):
    """Small, dependency-free JSON/static handler for one local process."""

    server_version = "Book2SkillDesktop/1.0"

    @property
    def context(self) -> _ServerContext:
        return cast(_DesktopHTTPServer, self.server).context

    def log_message(self, _format: str, *_args: object) -> None:
        # Avoid writing request paths (which may contain private filenames) to
        # the console or frozen application's log.
        return

    def _local_request(self) -> bool:
        server = cast(_DesktopHTTPServer, self.server)
        host = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{server.server_port}", "127.0.0.1"}
        if host not in expected:
            return False
        origin = self.headers.get("Origin")
        return not origin or origin in {
            f"http://127.0.0.1:{server.server_port}",
            f"http://localhost:{server.server_port}",
        }

    def _authorized(self, *, allow_query_token: bool = False) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        query_token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        header_token = self.headers.get("X-Book2Skill-Token", "")
        token = query_token if allow_query_token and query_token else header_token
        return self._local_request() and secrets.compare_digest(
            token, self.context.token
        )

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DesktopRequestError("Invalid Content-Length") from exc
        if length < 0 or length > MAX_JSON_BYTES:
            raise DesktopRequestError("Request body is too large")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DesktopRequestError("Request body must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise DesktopRequestError("Request body must be a JSON object")
        return cast(dict[str, object], parsed)

    def _send_static(self, relative: str) -> None:
        relative = relative or "index.html"
        candidate = (self.context.static_dir / relative).resolve()
        if (
            self.context.static_dir not in candidate.parents
            and candidate != self.context.static_dir
        ):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid static path"})
            return
        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        content_type = (
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        self._send_bytes(HTTPStatus.OK, content_type, candidate.read_bytes())

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._authorized(allow_query_token=True):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            if parsed.path == "/api/events":
                self._stream_events()
                return
            if parsed.path == "/api/bootstrap":
                self._send_json(HTTPStatus.OK, self.context.service.bootstrap())
                return
            if parsed.path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
        elif not self._local_request():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return
        relative = parsed.path.lstrip("/")
        self._send_static(relative)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self._authorized():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return
        try:
            payload = self._read_json()
            route = urllib.parse.urlparse(self.path).path
            if route in {"/api/jobs/analyze", "/api/jobs/build"}:
                kind = route.rsplit("/", 1)[-1]
                job_id = self.context.service.start_job(kind, payload)
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    {"accepted": True, "job_id": job_id},
                )
                return
            if route == "/api/jobs/cancel":
                self._send_json(
                    HTTPStatus.OK,
                    {"cancelled": self.context.service.cancel_job()},
                )
                return
            if route == "/api/install":
                self._send_json(HTTPStatus.OK, self.context.service.install(payload))
                return
            raise DesktopRequestError("Unknown API route")
        except JobBusyError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except DesktopRequestError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            # Do not return raw exception strings from an unexpected boundary.
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Desktop request failed"},
            )

    def _stream_events(self) -> None:
        subscriber = self.context.service.manager.events.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = subscriber.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                body = json.dumps(event, ensure_ascii=False).encode("utf-8")
                self.wfile.write(b"data: " + body + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.context.service.manager.events.unsubscribe(subscriber)


class DesktopWebServer:
    """Own the short-lived localhost server used by one WebGUI window."""

    def __init__(
        self,
        *,
        service: DesktopApplicationService | None = None,
        static_dir: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("Desktop WebGUI must bind to 127.0.0.1")
        self.service = service or DesktopApplicationService()
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.static_dir = static_dir or Path(__file__).with_name("static")
        self._httpd: _DesktopHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.base_url}/?token={urllib.parse.quote(self.token)}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = _DesktopHTTPServer((self.host, self.port), _Handler)
        self._httpd.context = _ServerContext(self.service, self.static_dir, self.token)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="book2skill-desktop-http",
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None


__all__ = ["DesktopWebServer"]
