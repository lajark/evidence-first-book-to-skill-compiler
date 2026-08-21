"""Windows desktop launcher for the local Book2Skill WebGUI."""

from __future__ import annotations

import atexit
from pathlib import Path

from book2skill.desktop.server import DesktopWebServer


class NativeApi:
    """Small pywebview bridge for native Windows file dialogs."""

    def pick_sources(self) -> list[str]:
        """Open a multi-file dialog without uploading source contents."""

        try:
            import webview
        except ImportError as exc:  # pragma: no cover - exercised in launcher
            raise RuntimeError(
                "The desktop extra is not installed; install book2skill[desktop]."
            ) from exc
        if not webview.windows:
            return []
        selected = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=(
                "Documents (*.pdf;*.epub;*.docx;*.html;*.htm;*.rtf;*.txt;*.md)",
                "All files (*.*)",
            ),
        )
        return [str(path) for path in (selected or [])]


def _static_dir() -> Path:
    return Path(__file__).with_name("static")


def main() -> None:
    """Launch the local WebGUI in a native pywebview window."""

    try:
        import webview
    except ImportError as exc:  # pragma: no cover - exercised in launcher
        raise SystemExit(
            "Desktop dependencies are missing. Install book2skill[desktop]."
        ) from exc

    server = DesktopWebServer(static_dir=_static_dir())
    server.start()
    atexit.register(server.stop)
    webview.create_window(
        "Book2Skill",
        url=server.url,
        js_api=NativeApi(),
        width=1240,
        height=860,
        min_size=(980, 680),
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["NativeApi", "main"]
