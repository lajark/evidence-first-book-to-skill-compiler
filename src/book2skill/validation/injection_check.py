"""Prompt-injection, hidden-character, suspicious-URL and path-traversal check.

Implements the security side of PRD FR-07 / FR-08 and SECURITY.md. The check
scans ``SKILL.md`` plus every ``references/*.md`` for four classes of risk:

1. **Injection text** — patterns that look like prompt-instruction attempts
   in both English and Chinese (e.g. ``ignore previous instructions``,
   ``忽略上述指令``, ``<|im_start|>``). Hits are ``fail``.

2. **Hidden characters** — zero-width spaces, BOM, control characters
   (C0/C1 except ``\\t\\n\\r``), RTL/LTR overrides. SECURITY.md treats all
   document content as data, so any hidden character that could alter
   interpretation is flagged. Hits are ``fail``.

3. **Suspicious URLs** — markdown links ``[text](url)`` whose scheme is not
   ``https`` (``http``/``ftp``/``file``/``data``/``javascript``) are ``warn``;
   ``data:``/``javascript:`` URLs are ``fail`` because they can execute in
   some hosts. Internal IP literals (loopback, link-local, RFC 1918) are
   ``warn``.

4. **Path traversal** — markdown links whose target contains ``..`` or an
   absolute path (Unix ``/`` or Windows ``C:\\``). Hits are ``fail``. The
   check also re-uses :func:`~book2skill.storage.file_storage.resolve_within`
   to confirm each link stays inside *skill_dir*.

The check is intentionally conservative: a single hit escalates the report to
``fail`` so a reviewer can decide whether it is a true positive.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from book2skill.storage.file_storage import resolve_within
from book2skill.validation.models import BaseCheck, CheckStatus, Finding

# ---------------------------------------------------------------------------
# 1. Injection text patterns
# ---------------------------------------------------------------------------

#: English prompt-injection patterns (case-insensitive). The list is
#: intentionally a starting point; per TODO it must be kept up to date.
_INJECTION_PATTERNS_EN: list[re.Pattern[str]] = [
    re.compile(r"\bignore\s+(?:previous|above|all|prior)\s+instructions?\b", re.I),
    re.compile(r"\bdisregard\s+(?:previous|above|all|prior)\s+instructions?\b", re.I),
    re.compile(r"\byou\s+are\s+(?:now|a|an)\b", re.I),
    re.compile(r"\b(?:system|assistant|user)\s*:", re.I),
    re.compile(r"<\|im_start\|>", re.I),
    re.compile(r"\[/INST\]", re.I),
    re.compile(r"\bnew\s+instructions?\s*:", re.I),
    re.compile(r"\bforget\s+(?:everything|all|previous)\b", re.I),
    re.compile(r"\bact\s+as\s+(?:a|an)\b", re.I),
]

#: Chinese prompt-injection patterns. ``re.I`` is a no-op for CJK but kept
#: for symmetry with the English list.
_INJECTION_PATTERNS_ZH: list[re.Pattern[str]] = [
    re.compile(r"忽略(?:上述|以上|前面|之前|所有)?(?:指令|指示|规则|约束)"),
    re.compile(r"无视(?:上述|以上|前面|之前|所有)?(?:指令|指示|规则|约束)"),
    re.compile(r"重新开始"),
    re.compile(r"你现在是"),
    re.compile(r"系统提示"),
    re.compile(r"角色设定"),
    re.compile(r"新(?:指令|指示)\s*[：:]"),
    re.compile(r"忘掉(?:一切|所有|前面|之前)"),
    re.compile(r"扮演(?:一个|一名)?"),
]


def _is_injection(text: str) -> str | None:
    """Return the matched pattern string if *text* looks injected, else ``None``"""
    for pat in _INJECTION_PATTERNS_EN:
        m = pat.search(text)
        if m:
            return m.group(0)
    for pat in _INJECTION_PATTERNS_ZH:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


# ---------------------------------------------------------------------------
# 2. Hidden characters
# ---------------------------------------------------------------------------

#: Zero-width characters and BOM.
_HIDDEN_ZERO_WIDTH = {
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\ufeff",  # zero-width no-break space / BOM
}

#: RTL / LTR override characters.
_HIDDEN_DIRECTIONAL = {
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # LRE/RLE/PDF/LRO/RLO
    "\u2066", "\u2067", "\u2068", "\u2069",  # LRI/RLI/FSI/PDI
}


def _hidden_char_code(ch: str) -> str | None:
    """Return a stable code for a hidden character, or ``None`` if benign."""
    if ch in _HIDDEN_ZERO_WIDTH:
        return "injection.zero_width"
    if ch in _HIDDEN_DIRECTIONAL:
        return "injection.directional_override"
    code = ord(ch)
    # C0 control chars except tab/newline/carriage-return.
    if code <= 0x1F and ch not in "\t\n\r":
        return "injection.control_char"
    # C1 control chars (0x7F DEL + 0x80-0x9F).
    if 0x7F <= code <= 0x9F:
        return "injection.control_char"
    return None


# ---------------------------------------------------------------------------
# 3. URL + path checks
# ---------------------------------------------------------------------------

#: Markdown link pattern: ``[label](target)``. Captures the target so we can
#: classify it as a URL (has a scheme) or a path (no scheme).
_MARKDOWN_LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)\)")

#: Schemes that are inherently dangerous when embedded in a Skill.
_DANGEROUS_SCHEMES = {"data", "javascript"}

#: Schemes we allow but warn about (non-https URLs).
_NON_HTTPS_SCHEMES = {"http", "ftp", "file"}

#: IPv4 internal ranges (loopback, link-local, RFC 1918). Conservative: a
#: ``localhost`` hostname also counts.
_INTERNAL_HOSTS = {"localhost", "0.0.0.0"}
_INTERNAL_IP_PREFIXES = ("127.", "10.", "169.254.", "192.168.")
#: 172.16.0.0/12 needs a numeric check because the prefix alone is ambiguous.


def _is_internal_host(host: str) -> bool:
    """True for loopback / link-local / RFC 1918 addresses or ``localhost``."""
    if host in _INTERNAL_HOSTS:
        return True
    if host.startswith(_INTERNAL_IP_PREFIXES):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) == 4 and parts[0] == "172":
            try:
                second = int(parts[1])
            except ValueError:
                return False
            return 16 <= second <= 31
    return False


def _classify_url(target: str) -> Finding | None:
    """Return a Finding for a suspicious URL, or ``None`` if benign."""
    # ``urlparse`` lower-cases the scheme for us.
    parsed = urlparse(target)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""

    if scheme in _DANGEROUS_SCHEMES:
        return Finding(
            severity=CheckStatus.FAIL,
            code="injection.dangerous_url",
            location=target,
            message=(
                f"Markdown link uses the '{scheme}' scheme, which can "
                "execute in some hosts; remove or replace with https."
            ),
        )
    if scheme and scheme != "https":
        return Finding(
            severity=CheckStatus.WARN,
            code="injection.non_https_url",
            location=target,
            message=(
                f"Markdown link uses the '{scheme}' scheme; prefer https."
            ),
        )
    if host and _is_internal_host(host):
        return Finding(
            severity=CheckStatus.WARN,
            code="injection.internal_url",
            location=target,
            message=(
                f"Markdown link points at internal host '{host}'; remove "
                "or replace with a public URL."
            ),
        )
    return None


def _classify_path(target: str, skill_dir: Path) -> Finding | None:
    """Return a Finding for a path-traversing link, or ``None`` if benign."""
    if not target:
        return None
    # Absolute Unix path or Windows drive letter.
    if target.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", target):
        return Finding(
            severity=CheckStatus.FAIL,
            code="injection.absolute_path",
            location=target,
            message=(
                f"Markdown link target '{target}' is an absolute path; "
                "use a path relative to the Skill directory."
            ),
        )
    if ".." in target.split("/"):
        return Finding(
            severity=CheckStatus.FAIL,
            code="injection.path_traversal",
            location=target,
            message=(
                f"Markdown link target '{target}' contains '..'; links must "
                "stay inside the Skill directory."
            ),
        )
    # Final guard: resolve under skill_dir and reject escapes.
    try:
        resolve_within(skill_dir, *target.split("/"))
    except Exception:
        return Finding(
            severity=CheckStatus.FAIL,
            code="injection.path_escape",
            location=target,
            message=(
                f"Markdown link target '{target}' escapes the Skill "
                "directory."
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


class InjectionCheck(BaseCheck):
    """Scan content for injection, hidden chars, suspicious URLs and traversal."""

    check_id = "injection"

    def _run(self, skill_dir: Path) -> list[Finding]:
        findings: list[Finding] = []
        targets: list[tuple[Path, str]] = []
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            targets.append((skill_md, "SKILL.md"))
        references_dir = skill_dir / "references"
        if references_dir.is_dir():
            for ref_file in sorted(references_dir.glob("*.md")):
                rel = ref_file.relative_to(skill_dir).as_posix()
                targets.append((ref_file, rel))

        for path, rel in targets:
            text = path.read_text(encoding="utf-8")
            self._scan_text(text, rel, skill_dir, findings)
        return findings

    def _scan_text(
        self,
        text: str,
        rel_path: str,
        skill_dir: Path,
        findings: list[Finding],
    ) -> None:
        """Run all four sub-scans against a single file's text."""
        line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                line_starts.append(i + 1)

        # 1. Injection phrases — scan line by line so we can report a line no.
        for line_no, line in enumerate(text.splitlines(), start=1):
            hit = _is_injection(line)
            if hit:
                findings.append(
                    Finding(
                        severity=CheckStatus.FAIL,
                        code="injection.phrase",
                        location=f"{rel_path}:{line_no}",
                        message=(
                            f"Possible prompt-injection text: '{hit}'. "
                            "Treat source content as data and review."
                        ),
                    )
                )

        # 2. Hidden characters — scan character by character, dedup by code+line.
        seen: set[tuple[str, int]] = set()
        for offset, ch in enumerate(text):
            code = _hidden_char_code(ch)
            if code is None:
                continue
            line_no = self._line_for_offset(offset, line_starts)
            key = (code, line_no)
            if key in seen:
                continue
            seen.add(key)
            char_repr = self._char_repr(ch)
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code=code,
                    location=f"{rel_path}:{line_no}",
                    message=(
                        f"Hidden character {char_repr} (U+{ord(ch):04X}) "
                        "found; remove before publishing."
                    ),
                )
            )

        # 3. + 4. URL + path traversal — scan markdown links.
        for match in _MARKDOWN_LINK_RE.finditer(text):
            target = match.group("target")
            line_no = self._line_for_offset(match.start(), line_starts)
            location = f"{rel_path}:{line_no}"
            url_finding = _classify_url(target)
            if url_finding is not None:
                findings.append(
                    Finding(
                        severity=url_finding.severity,
                        code=url_finding.code,
                        location=location,
                        message=url_finding.message,
                    )
                )
                continue
            # Only treat as a path when there's no URL scheme.
            parsed = urlparse(target)
            if parsed.scheme:
                continue
            path_finding = _classify_path(target, skill_dir)
            if path_finding is not None:
                findings.append(
                    Finding(
                        severity=path_finding.severity,
                        code=path_finding.code,
                        location=location,
                        message=path_finding.message,
                    )
                )

    @staticmethod
    def _line_for_offset(offset: int, line_starts: list[int]) -> int:
        import bisect

        return bisect.bisect_right(line_starts, offset)

    @staticmethod
    def _char_repr(ch: str) -> str:
        """Return a printable representation of a hidden character."""
        names = {
            "\u200b": "ZWSP",
            "\u200c": "ZWNJ",
            "\u200d": "ZWJ",
            "\u2060": "WJ",
            "\ufeff": "BOM",
            "\u202a": "LRE",
            "\u202b": "RLE",
            "\u202c": "PDF",
            "\u202d": "LRO",
            "\u202e": "RLO",
            "\u2066": "LRI",
            "\u2067": "RLI",
            "\u2068": "FSI",
            "\u2069": "PDI",
        }
        return names.get(ch, "CTRL")


__all__ = ["InjectionCheck"]
