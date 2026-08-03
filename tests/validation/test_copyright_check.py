"""Tests for :mod:`book2skill.validation.copyright_check`."""

from __future__ import annotations

from pathlib import Path

from book2skill.validation.copyright_check import CopyrightCheck
from book2skill.validation.models import CheckStatus

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)


def _make_skill_with_quote(skill_dir: Path, quote: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
    )
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(exist_ok=True)
    content = (
        "# Techniques\n\n## u1\n\nSome text.\n\n"
        f"**Sources:**\n- src-1 / block-a — \"{quote}\"\n"
    )
    (refs_dir / "techniques.md").write_text(content, encoding="utf-8")
    return skill_dir


class TestCopyrightCheck:
    def test_short_english_quote_passes(self, tmp_path: Path) -> None:
        _make_skill_with_quote(tmp_path, "This is a short quote of few words.")
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS
        assert result.evidence == []

    def test_long_english_quote_warns(self, tmp_path: Path) -> None:
        quote = " ".join(f"word{i}" for i in range(30))
        _make_skill_with_quote(tmp_path, quote)
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.WARN
        assert "copyright.long_quote" in result.evidence

    def test_very_long_english_quote_fails(self, tmp_path: Path) -> None:
        quote = " ".join(f"word{i}" for i in range(45))
        _make_skill_with_quote(tmp_path, quote)
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "copyright.quote_too_long" in result.evidence

    def test_short_chinese_quote_passes(self, tmp_path: Path) -> None:
        # 10 Chinese chars ~ ceil(10/1.5) = 7 effective words; under cap.
        _make_skill_with_quote(tmp_path, "这是一段简短的中文引用内容。")
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS

    def test_long_chinese_quote_warns(self, tmp_path: Path) -> None:
        # 50 Chinese chars ~ ceil(50/1.5) = 34 effective words → warn (>25).
        quote = "这" * 50
        _make_skill_with_quote(tmp_path, quote)
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.WARN
        assert "copyright.long_quote" in result.evidence

    def test_mixed_quote_passes_when_short(self, tmp_path: Path) -> None:
        # 5 English words + ~9 Chinese chars (~6 effective) = ~11 total.
        quote = "hello world foo bar baz 这是一段中文测试。"
        _make_skill_with_quote(tmp_path, quote)
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS

    def test_no_quote_skipped(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        (refs_dir / "techniques.md").write_text(
            "# Techniques\n\n## u1\n\nNo quote here.\n\n"
            "**Sources:**\n- src-1 / block-a\n",
            encoding="utf-8",
        )
        result = CopyrightCheck().run(skill_dir)
        assert result.status == CheckStatus.PASS

    def test_boundary_at_cap_passes(self, tmp_path: Path) -> None:
        # Exactly 25 words: at cap (not over) → pass.
        quote = " ".join(f"w{i}" for i in range(25))
        _make_skill_with_quote(tmp_path, quote)
        result = CopyrightCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS

    def test_contiguous_source_blocks_are_aggregated(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        quote = " ".join(f"w{i}" for i in range(25))
        (refs_dir / "a.md").write_text(
            f'- src-1 / block-p1 — "{quote}"\n', encoding="utf-8"
        )
        (refs_dir / "b.md").write_text(
            f'- src-1 / block-p2 — "{quote}"\n', encoding="utf-8"
        )

        result = CopyrightCheck().run(skill_dir)

        assert result.status == CheckStatus.FAIL
        assert "copyright.aggregate_quote_too_long" in result.evidence

    def test_repeated_excerpt_across_files_is_flagged(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        quote = " ".join(f"repeat{i}" for i in range(25))
        for name in ("a.md", "b.md"):
            (refs_dir / name).write_text(
                f'- src-1 / {name[:-3]} — "{quote}"\n', encoding="utf-8"
            )

        result = CopyrightCheck().run(skill_dir)

        assert result.status == CheckStatus.FAIL
        assert "copyright.duplicate_quote_too_long" in result.evidence

    def test_near_duplicate_excerpt_is_flagged(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        first = " ".join(f"token{i}" for i in range(25))
        second = first.replace("token12", "edited12")
        (refs_dir / "a.md").write_text(
            f'- src-1 / block-a — "{first}"\n', encoding="utf-8"
        )
        (refs_dir / "b.md").write_text(
            f'- src-1 / block-b — "{second}"\n', encoding="utf-8"
        )

        result = CopyrightCheck().run(skill_dir)

        assert result.status == CheckStatus.FAIL
        assert "copyright.similar_quote_too_long" in result.evidence

    def test_reordered_sentences_are_caught_by_phrase_fingerprints(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        first_sentence = " ".join(f"first{i}" for i in range(10))
        second_sentence = " ".join(f"second{i}" for i in range(11))
        first = f"{first_sentence}. {second_sentence}."
        second = f"{second_sentence}. {first_sentence}."
        (refs_dir / "a.md").write_text(
            f'- src-1 / block-a — "{first}"\n', encoding="utf-8"
        )
        (refs_dir / "b.md").write_text(
            f'- src-1 / block-b — "{second}"\n', encoding="utf-8"
        )

        result = CopyrightCheck().run(skill_dir)

        assert result.status == CheckStatus.FAIL
        assert "copyright.fingerprint_similar_quote_too_long" in result.evidence

    def test_quote_in_skill_body_is_checked(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        quote = " ".join(f"word{i}" for i in range(45))
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER
            + f'# Skill\n\n**Sources:**\n- src-1 / block-a — "{quote}"\n',
            encoding="utf-8",
        )

        result = CopyrightCheck().run(skill_dir)

        assert result.status == CheckStatus.FAIL
        assert "copyright.quote_too_long" in result.evidence

    def test_quote_in_wiki_body_is_checked(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        wiki_dir = skill_dir / "wiki"
        wiki_dir.mkdir()
        quote = " ".join(f"word{i}" for i in range(30))
        (wiki_dir / "chapters.md").write_text(
            f'## Chapter\n\n**Sources:**\n- src-1 / block-a — "{quote}"\n',
            encoding="utf-8",
        )

        result = CopyrightCheck().run(skill_dir)

        assert result.status == CheckStatus.WARN
        assert "copyright.long_quote" in result.evidence

    def test_no_references_dir_passes(self, tmp_path: Path) -> None:
        skill_dir = tmp_path
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
        )
        result = CopyrightCheck().run(skill_dir)
        assert result.status == CheckStatus.PASS
