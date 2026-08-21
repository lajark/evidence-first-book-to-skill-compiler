"""Windows desktop launcher for the local Book2Skill WebGUI."""

from __future__ import annotations

import atexit
import sys
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
    """Resolve bundled WebGUI assets in source and frozen layouts.

    PyInstaller does not guarantee that a frozen module's ``__file__`` points
    at a materialised source file.  In that layout the asset directory lives
    below ``sys._MEIPASS`` (or the onedir ``_internal`` directory), so using
    only ``Path(__file__).with_name`` can silently give the HTTP server a
    non-existent directory and render a blank window.
    """

    candidates = [Path(__file__).with_name("static")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "book2skill" / "desktop" / "static")
    executable_dir = Path(sys.executable).resolve().parent
    candidates.append(
        executable_dir / "_internal" / "book2skill" / "desktop" / "static"
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


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
